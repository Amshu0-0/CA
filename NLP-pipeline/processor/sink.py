# Where the output of the engine goes. MemorySink keeps it in dictionaries (the tests and scripts/local-replay.py use it); MongoSink writes it
# to MongoDB (processor.py uses it). Both do the same things, so what the tests prove about one holds for the other:
#   - events and windows: one document per _id, replaced as a whole. Writing the same document twice changes nothing, so a batch that is
#     delivered again after a crash cannot create duplicates.
#   - runs: the "set" fields are always written; the "insert" fields only when the run's document does not exist yet.
# The processor writes events, then windows, then runs, and only then tells Kafka the batch is done (the order is the one that makes a
# restart safe; tests/test_restart.py crashes the processor between every two of these steps).

import copy


# The two kinds of fields of a run update, without a field in both (MongoDB refuses that). "set" wins.
def run_fields(parts):
    to_set = dict(parts["set"])
    to_insert = {key: value for key, value in parts["insert"].items() if key not in to_set}
    return to_set, to_insert


# Everything in dictionaries. Documents are copied on the way in and out, as a database would.
class MemorySink:
    def __init__(self):
        self.events = {}
        self.windows = {}
        self.runs = {}

    def write_events(self, documents):
        for document in documents:
            self.events[document["_id"]] = copy.deepcopy(document)

    def write_windows(self, documents):
        for document in documents:
            self.windows[document["_id"]] = copy.deepcopy(document)

    def write_runs(self, runs):
        for run, parts in runs.items():
            to_set, to_insert = run_fields(parts)
            document = self.runs.get(run)

            if document is None:
                document = {"_id": run}
                document.update(copy.deepcopy(to_insert))
                self.runs[run] = document

            document.update(copy.deepcopy(to_set))

    def write(self, out):
        self.write_events(out.events)
        self.write_windows(out.windows)
        self.write_runs(out.runs)

    # What was stored for a run before: the grid position that the windower needs to carry on (None if the run is new).
    def load_state(self, run):
        return state_of(self.runs.get(run))


STATE_FIELDS = ("origin", "next_k", "last_key", "done")


def state_of(document):
    if document is None:
        return None

    return {key: document[key] for key in STATE_FIELDS if key in document}


# MongoDB through pymongo. `database` is a pymongo Database (or mongomock's, in the tests).
class MongoSink:
    def __init__(self, database, events="events", windows="windows", runs="runs"):
        self.events = database[events]
        self.windows = database[windows]
        self.runs = database[runs]

    @staticmethod
    def _replace(collection, documents):
        from pymongo import ReplaceOne

        if documents:
            collection.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in documents], ordered=False)

    def write_events(self, documents):
        self._replace(self.events, documents)

    def write_windows(self, documents):
        self._replace(self.windows, documents)

    def write_runs(self, runs):
        from pymongo import UpdateOne

        operations = []

        for run, parts in runs.items():
            to_set, to_insert = run_fields(parts)
            update = {}

            if to_set:
                update["$set"] = to_set

            if to_insert:
                update["$setOnInsert"] = to_insert

            if update:
                operations.append(UpdateOne({"_id": run}, update, upsert=True))

        if operations:
            self.runs.bulk_write(operations, ordered=True)

    def write(self, out):
        self.write_events(out.events)
        self.write_windows(out.windows)
        self.write_runs(out.runs)

    def load_state(self, run):
        return state_of(self.runs.find_one({"_id": run}, {key: 1 for key in STATE_FIELDS}))
