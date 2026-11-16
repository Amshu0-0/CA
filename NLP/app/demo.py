"""Interactive demo of LogBERT on BGL log windows.

Paste a log line - or insert an alert message into a normal window - and see
LogBERT's verdict and which of the window's keys it failed to predict.

Usage:
    python app/demo.py                                # artifacts of configs/bgl.yaml
    python app/demo.py --config configs/bgl_2k.yaml   # smoke-test artifacts
    python app/demo.py --share                        # temporary public link
"""

from __future__ import annotations

import argparse

import gradio as gr
import pandas as pd

from logad.config import add_config_arguments, config_from_args
from logad.demo import DemoEngine, WindowAnalysis
from logad.utils import setup_logging

INTRO = """
# System log anomaly detection with LogBERT
LogBERT learns which *sequences of log templates* are normal. It hides some keys of a window, guesses
them from the rest, and flags the window when more than r hidden keys are not among its top-g guesses.
Pasted BGL lines have their alert tag removed before the model sees them.
"""


# Parse command-line arguments for the demo.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive log anomaly detection demo")
    add_config_arguments(parser)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to serve on the network")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true", help="Create a public Gradio link")
    return parser.parse_args()


# Render the parsed input and the verdict as Markdown cards.
def format_verdict(analysis: WindowAnalysis) -> str:
    parts = []
    if analysis.parsed is not None:
        parsed = analysis.parsed
        template = (
            f"`{parsed.template}`"
            if parsed.template_id >= 0
            else f"unseen template - masked text `{parsed.template}`"
        )
        removed = (
            f" Alert tag removed before analysis: `{parsed.removed_tag}`."
            if parsed.removed_tag
            else ""
        )
        parts.append(f"**Model input:** `{parsed.content}`  \n**Template:** {template}.{removed}")
    verdict = analysis.verdict
    if not verdict.available:
        status = "_not available_"
    elif verdict.anomalous:
        status = "**ANOMALOUS**"
    else:
        status = "**normal**"
    parts.append(f"### {verdict.name}\n{status} - {verdict.summary}")
    return "\n\n".join(parts)


# Build the two-tab Gradio interface around a loaded DemoEngine.
def build_ui(engine: DemoEngine) -> gr.Blocks:
    windows = engine.candidate_windows()
    choices = [
        (f"window {row.window_id} - {row.n_messages} messages", int(row.window_id))
        for row in windows.itertuples()
    ]
    longest = int(windows["n_messages"].max()) if not windows.empty else 1

    # Callback for tab 1: one pasted line, inserted into a reference window.
    def on_line(line: str) -> tuple[str, pd.DataFrame]:
        if not line or not line.strip():
            return "Paste a log line first.", pd.DataFrame()
        analysis = engine.analyze_line(line)
        return format_verdict(analysis), analysis.messages

    # Callback for tab 2: a chosen window, optionally with an inserted line.
    def on_window(window_id: int, line: str, position: float) -> tuple[str, pd.DataFrame]:
        if window_id is None:
            return "No normal test window available.", pd.DataFrame()
        analysis = engine.analyze_window(int(window_id), line, int(position))
        return format_verdict(analysis), analysis.messages

    with gr.Blocks(title="LogBERT log anomaly detection") as demo:
        gr.Markdown(INTRO)

        with gr.Tab("Paste a log line"):
            line = gr.Textbox(
                label="Log line",
                placeholder="A raw BGL line (its alert tag is removed) or just the message text",
            )
            analyze = gr.Button("Analyze", variant="primary")
            verdicts = gr.Markdown()
            table = gr.Dataframe(label="Reference normal window with your line inserted")
            examples = engine.example_lines()
            if examples:
                gr.Examples(examples=[[e] for e in examples], inputs=line)
            analyze.click(on_line, inputs=line, outputs=[verdicts, table])
            line.submit(on_line, inputs=line, outputs=[verdicts, table])

        with gr.Tab("Insert into a window"):
            window = gr.Dropdown(
                choices=choices,
                value=choices[0][1] if choices else None,
                label="Normal window from the test period",
            )
            inserted = gr.Textbox(
                label="Message to insert (leave empty to analyse the window as is)"
            )
            position = gr.Slider(
                0, longest, value=min(5, longest), step=1, label="Insert before message #"
            )
            run = gr.Button("Analyze window", variant="primary")
            window_verdicts = gr.Markdown()
            window_table = gr.Dataframe(label="Messages in the window")
            run.click(
                on_window,
                inputs=[window, inserted, position],
                outputs=[window_verdicts, window_table],
            )
    return demo


# Entry point: load all artifacts once, then serve the UI.
def main() -> None:
    setup_logging()
    args = parse_args()
    engine = DemoEngine(config_from_args(args))
    build_ui(engine).launch(server_name=args.host, server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
