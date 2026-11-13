| # | Label | Rule | Keywords | Train templates (correct/matched) | Train messages |
|---:|---|---|---|---:|---:|
| 1 | normal | Describes normal system operations or status updates. | `<*>:<*>eeeeeee`, `<*>:<*>d<*>e<*>c<*>`, `<*>:<*>da<*>a<*>`, `<*> max=<*`, `program interrupt`, `input interrupt enable` | 37/37 | 93,124 |
| 2 | normal | Log messages related to normal operations or status updates. | `pro`, `total of <*> ddr error(s) detected and corrected`, `state register: machine state register:`, `disable all access to cache directory....`, `message=scomerror` | 35/35 | 314,312 |
| 3 | abnormal | Indicates a system error or failure. | `machine check interrupt`, `mmcs_server exited abnormally`, `monitor caught java.lang.unsupportedoperationexception`, `error: unable to mount filesystem`, `lustre mount failed`, `ciod: error reading message prefix`, `rts: kernel terminated for reason`, `no ethernet link`, `kernel panic` | 14/14 | 65,338 |
| 4 | abnormal | Messages mentioning 'FAILED', 'terminated for reason', 'kernel panic', and 'machine check interrupt' are indicative of system failures. | `lustre mount failed`, `rts: kernel terminated for reason`, `kernel panic`, `machine check interrupt` | 9/9 | 44,831 |
| 5 | normal | Indicates a normal operation or system state. | `machine state register`, `byte ordering exception`, `while initializing node card, ido with lp of`, `ciod: missing or invalid fields on line`, `disable apu instruction broadcast` | 9/9 | 20,839 |
| 6 | abnormal | Points out a critical system failure that requires immediate attention. | `kernel terminated for reason`, `no ethernet link`, `mmcs_server exited abnormally due to signal: segmentation fault`, `rts panic! - stopping execution`, `ciod: error reading message prefix after` | 8/8 | 40,827 |
| 7 | abnormal | Signals an execution stop due to panic or kernel issues. | `rts panic!`, `ciod: error reading message prefix`, `machine check interrupt`, `mmcs_server exited abnormally`, `kernel panic`, `monitor caught java.lang.unsupportedoperationexception`, `error: unable to mount filesystem` | 8/8 | 24,445 |
| 8 | abnormal | Indicates a system or process has stopped unexpectedly. | `terminated`, `panic! - stopping execution`, `exited abnormally` | 7/7 | 21,060 |
| 9 | abnormal | Log messages containing 'terminated', 'panic!', or 'exited abnormally' indicate critical system failures. | `terminated`, `panic!`, `exited abnormally` | 7/7 | 21,060 |
| 10 | abnormal | Messages containing 'terminated', 'panic!', 'stopping execution', or 'exited abnormally' are likely abnormal. | `terminated`, `panic!`, `stopping execution`, `exited abnormally` | 7/7 | 21,060 |
| 11 | abnormal | Signals system or hardware issues that require attention. | `machine check interrupt`, `power module status fault detected`, `kernel terminated for reason`, `midplane switch controller failure` | 7/7 | 17,268 |
| 12 | normal | Indicates a communication failure between nodes or services. | `problem communicating with service card`, `no alerts are active` | 7/7 | 16,196 |
| 13 | abnormal | Messages indicating abnormal termination or exit. | `terminated for reason`, `exited abnormally due to signal: aborted`, `exited normally with exit code` | 6/6 | 17,213 |
| 14 | abnormal | Log messages containing 'exited abnormally' or 'terminated for reason' indicate abnormal termination. | `exited abnormally`, `terminated for reason` | 6/6 | 17,205 |
| 15 | abnormal | Indicates a kernel termination or abnormal exit. | `terminated`, `exited abnormally` | 6/6 | 17,205 |
| 16 | abnormal | Log messages with 'parityAlignment', 'rts: bad message header', and 'java.lang.NullPointerException' are indicative of software or configuration errors. | `midplaneswitchcontroller::parityalignment`, `rts: kernel terminated for reason`, `while inserting monitor info into db caught java.lang.nullpointerexception` | 6/6 | 17,203 |
| 17 | normal | Messages including 'not accessible', 'not fully functional', or 'subnets and addresses added' are typically informational or setup-related. | `not accessible`, `not fully functional`, `subnets and addresses added` | 6/6 | 16,898 |
| 18 | normal | Signals the start of a process or service. | `has been started`, `started:` | 6/6 | 195 |
| 19 | normal | Any message containing 'ASSERT condition', 'chip, clear all resets', and 'java.io.IOException: Problems with the chip' are normal operational messages. | `assert condition`, `chip, clear all resets`, `java.io.ioexception: problems with the chip` | 5/5 | 84,207 |
| 20 | abnormal | Indicates a system or service failure. | `terminated for reason`, `exited abnormally due to signal: aborted` | 5/5 | 17,202 |
| 21 | abnormal | Signals an issue with the kernel or operating system. | `kernel terminated for reason`, `exited abnormally due to signal: aborted` | 5/5 | 17,202 |
| 22 | abnormal | Presence of 'terminated for reason' or 'kernel panic' | `terminated for reason`, `kernel panic` | 5/5 | 17,199 |
| 23 | abnormal | Signals an abnormal termination of the kernel or process execution. | `kernel terminated for reason`, `monitor caught java.lang.unsupportedoperationexception` | 5/5 | 17,189 |
| 24 | abnormal | Messages mentioning 'machine check interrupt', 'power module status fault detected', and 'TLB error interrupt' are typically abnormal. | `machine check interrupt`, `power module status fault detected`, `tlb error interrupt` | 4/4 | 152,804 |
| 25 | abnormal | Error or failure messages related to file systems, mounts, and services. | `unable to mount filesystem`, `lustre mount failed`, `midplaneswitchcontroller::sendtrain() port disconnected` | 4/4 | 28,296 |
| 26 | abnormal | Messages mentioning 'deactivated', 'mount FAILED', or 'connection lost' are usually abnormal. | `power deactivated`, `lustre mount failed`, `bglerr_ido_pkt_timeout connection lost to node/link/service card` | 4/4 | 27,756 |
| 27 | abnormal | Any message with 'kernel terminated for reason' is an indication of a critical failure, thus marked as abnormal. | `kernel terminated for reason` | 4/4 | 17,187 |
| 28 | abnormal | Log messages containing 'terminated for reason' indicate abnormal conditions. | `terminated for reason` | 4/4 | 17,187 |
| 29 | abnormal | Signals the termination of a kernel for unspecified reasons. | `kernel terminated for reason`, `rts: kernel terminated for reason` | 4/4 | 17,187 |
| 30 | normal | Describes a state or condition without indicating an error. | `machine state register:` | 4/4 | 8,407 |
| 31 | normal | Describes normal operational messages or informational updates. | `ddr failing info register:`, `ciod: <*> <*> <*> <*> <*> <*> <*> at line`, `error getting detailed hw info for node, caught java.io.ioexception:` | 4/4 | 4,020 |
| 32 | abnormal | Messages mentioning 'socket closed', 'connection lost', 'segmentation fault', and 'uncorrectable torus error' indicate issues with communication or hardware, thus are abnormal. | `socket closed`, `connection lost`, `segmentation fault`, `uncorrectable torus error` | 4/4 | 134 |
| 33 | abnormal | Indicates an error interrupt with specific details. | `machine check interrupt (bit=`, `error interrupt` | 3/3 | 152,803 |
| 34 | abnormal | Signals an error interrupt or critical system message that requires attention. | `error interrupt`, `machine check interrupt` | 3/3 | 152,803 |
| 35 | abnormal | Messages mentioning 'TLB error interrupt', 'L<*> dcache unit data parity error', and 'parityAlignment' are indicative of hardware issues. | `tlb error interrupt`, `l<*> dcache unit data parity error`, `parityalignment` | 3/3 | 152,769 |
| 36 | abnormal | Signals an error in the file system mounting process. | `error: unable to mount filesystem`, `lustre mount failed` | 3/3 | 28,272 |
| 37 | abnormal | Points to file system or storage problems. | `lustre mount failed`, `unable to mount filesystem` | 3/3 | 28,272 |
| 38 | abnormal | Log messages that include 'Lustre mount FAILED' or 'MidplaneSwitchController::sendTrain() port disconnected' suggest system issues and are labeled as abnormal. | `lustre mount failed`, `midplaneswitchcontroller::sendtrain() port disconnected` | 3/3 | 27,576 |
| 39 | abnormal | Points to a communication or network issue. | `socket closed`, `ciostream socket` | 3/3 | 25,873 |
| 40 | abnormal | Indicates a failure in communication or data transfer, which can be a sign of an underlying issue. | `monitor caught java.lang.unsupportedoperationexception`, `ciod: failed to read message prefix on control stream`, `ciod: error reading message prefix after <*>` | 3/3 | 25,758 |
| 41 | abnormal | Indicates a network or socket issue. | `no ethernet link`, `socket closed`, `error reading message prefix after` | 3/3 | 19,899 |
| 42 | abnormal | Denotes an assertion failure or system panic. | `microloader assertion`, `rts panic! - stopping execution`, `mmcs_server exited abnormally due to signal: segmentation fault` | 3/3 | 5,361 |
| 43 | abnormal | Signals that the system is in a state where it needs to be addressed, often due to an error or failure. | `rts panic! - stopping execution`, `no ethernet link`, `socket closed` | 3/3 | 3,981 |
| 44 | abnormal | Signals a critical system error or crash. | `bglerr_ido_pkt_timeout connection lost`, `mmcs_server exited abnormally due to signal:`, `rts panic! - stopping execution` | 3/3 | 3,870 |
| 45 | abnormal | Points to a critical system state change or failure. | `connection lost`, `socket closed`, `no ethernet link` | 3/3 | 138 |
| 46 | normal | Describes a warning or alert that does not necessarily indicate an immediate failure but requires monitoring. | `alert is (are) active`, `ddr: unable to steer rank`, `job timed out` | 3/3 | 99 |
| 47 | abnormal | Machine check interrupts and segmentation faults | `machine check interrupt`, `segmentation fault` | 3/3 | 83 |
| 48 | abnormal | Log messages with 'machine check interrupt' or 'power module status fault detected' are abnormal. | `machine check interrupt`, `power module status fault detected` | 3/3 | 81 |
| 49 | normal | Messages stating 'disable flagging of DDR UE's', 'write buffer commit threshold', and 'disable write lines' are normal administrative notes or settings changes. | `disable flagging of ddr ue's`, `write buffer commit threshold`, `disable write lines` | 3/3 | 50 |
| 50 | abnormal | Log messages containing 'exited' with 'abnormally' or an exit code indicate abnormal termination. | `exited abnormally`, `exited normally with exit code` | 3/3 | 29 |
| 51 | abnormal | Log messages containing 'exited' with 'abnormally' or 'normal' trigger the respective label. | `exited abnormally`, `exited normally` | 3/3 | 29 |
| 52 | abnormal | Describes an error condition that requires immediate attention, often related to hardware issues. | `monitor caught java.lang.unsupportedoperationexception`, `mmcs_server exited abnormally due to signal: segmentation fault`, `external input interrupt (unit=<*> bit=<*>): uncorrectable torus error` | 3/3 | 7 |
| 53 | normal | Log entries that contain 'alignment exceptions' are typically normal, as they refer to expected software behavior. | `alignment exceptions` | 2/2 | 480,012 |
| 54 | abnormal | Messages mentioning 'Lustre mount FAILED' are related to file system issues and should be labeled as abnormal. | `lustre mount failed` | 2/2 | 27,552 |
| 55 | abnormal | Indicates a failure in reading or processing messages on the CioStream socket, which is critical for communication between nodes. | `failed to read message prefix`, `error reading message prefix` | 2/2 | 25,756 |
| 56 | abnormal | Signals a timeout or failure in reading messages from a stream socket. | `bglerr_ido_pkt_timeout`, `failed to read message prefix on control stream` | 2/2 | 5,995 |
| 57 | abnormal | Indicates a system or module is stopping, which could be due to an error. | `stopping execution`, `not present and is stopping` | 2/2 | 3,857 |
| 58 | abnormal | Indicates a system stopping or critical error. | `stopping`, `not present` | 2/2 | 3,857 |
| 59 | abnormal | Log messages with 'assertion' or 'null pointer exception' are abnormal. | `microloader assertion`, `while inserting monitor info into db caught java.lang.nullpointerexception` | 2/2 | 1,505 |
| 60 | normal | System startup and initialization messages. | `starting systemcontroller`, `prepareforservice is being done on this midplane` | 2/2 | 216 |
| 61 | abnormal | Indicates a critical system state change or failure. | `power deactivated`, `connection lost to node/link/service card` | 2/2 | 204 |
| 62 | abnormal | Signals a critical system state change or failure. | `power deactivated`, `power module status fault detected` | 2/2 | 193 |
| 63 | abnormal | Error messages related to network or communication issues | `no ethernet link`, `receivetrain() iap failed:` | 2/2 | 167 |
| 64 | abnormal | Denotes a hardware or communication failure involving node cards, midplane switches, and IAP failures. | `power module status fault detected on node card`, `midplaneswitchcontroller::receivetrain()`, `iap failed: r-` | 2/2 | 159 |
| 65 | abnormal | Messages mentioning 'connection lost', 'socket closed', or 'link card not fully functional' suggest network or hardware issues. | `connection lost`, `socket closed`, `link card not fully functional` | 2/2 | 129 |
| 66 | abnormal | Describes network or communication issues. | `socket closed`, `no ethernet link` | 2/2 | 126 |
| 67 | abnormal | Log messages with 'machine check interrupt' and specific error details are flagged as abnormal. | `machine check interrupt`, `data parity error` | 2/2 | 80 |
| 68 | abnormal | Messages that start with 'machine check interrupt (bit=)' indicate an abnormal condition. | `machine check interrupt (bit=` | 2/2 | 80 |
| 69 | abnormal | Indicates an abnormal termination of processes with a specific exit code. | `exited abnormally due to signal: aborted`, `exited normally with exit code` | 2/2 | 26 |
| 70 | abnormal | Signals an abnormal termination of a process due to a specific signal. | `exited abnormally due to signal:` | 2/2 | 18 |
| 71 | abnormal | System crashes or critical failures indicated by 'exited abnormally' | `exited abnormally` | 2/2 | 18 |
| 72 | abnormal | Signals an uncorrectable error in the torus network structure. | `uncorrectable torus error`, `tree header with no target waiting` | 2/2 | 3 |
| 73 | normal | Indicates a service network issue or an empty database. | `declareservicenetworkcharacteristics`, `db is not empty` | 2/2 | 2 |
| 74 | normal | Refers to the generation of core files, which is normal behavior during debugging. | `generating core` | 1/1 | 1,706,751 |
| 75 | abnormal | Indicates a system or process has stopped execution. | `panicked`, `panic!` | 1/1 | 3,855 |
| 76 | abnormal | Indicates a system or process has stopped execution. | `stopping execution`, `panic! - stopping execution` | 1/1 | 3,855 |
| 77 | normal | Indicates a service or action is being performed. | `as part of service action` | 1/1 | 2,600 |
| 78 | normal | Any log mentioning 'cr:' and 'xer:' likely indicates a system error but not necessarily an issue. | `cr:<*> xer:<*>` | 1/1 | 2,258 |
| 79 | abnormal | Signals an assertion failure in the system, indicating a serious error. | `microloader assertion`, `assertion` | 1/1 | 1,503 |
| 80 | abnormal | Signals an assertion failure, which is typically a programming error. | `microloader assertion` | 1/1 | 1,503 |
| 81 | abnormal | Errors related to file system or mount issues | `unable to mount filesystem`, `lustre mount failed:` | 1/1 | 720 |
| 82 | abnormal | Indicates an error in mounting a filesystem. | `error: unable to mount filesystem` | 1/1 | 720 |
| 83 | abnormal | Any log with 'power deactivated' is an alert. | `power deactivated:` | 1/1 | 192 |
| 84 | normal | Indicates a failure or timeout event related to job execution. | `job timed out`, `timed out. block freed.` | 1/1 | 186 |
| 85 | abnormal | Log messages indicating a 'midplane switch controller' issue are labeled as abnormal. | `midplaneswitchcontroller::sendtrain() port disconnected` | 1/1 | 24 |
| 86 | abnormal | Points to a hardware failure or disconnection. | `port disconnected:`, `midplane switch controller` | 1/1 | 24 |
| 87 | normal | Describes normal system operations or status updates. | `<*>:fe<*>c<*>`, `command manager unit summary` | 1/1 | 16 |
| 88 | abnormal | Signals an abnormal termination due to specific signals. | `exited abnormally due to signal: aborted` | 1/1 | 15 |
| 89 | abnormal | Signals a critical system event that needs immediate action. | `kernel panic` | 1/1 | 12 |
| 90 | abnormal | Signals an error related to communication or connection issues. | `connection lost to node/link/service card`, `bglerr_ido_pkt_timeout` | 1/1 | 12 |
| 91 | abnormal | Signals an abnormal exit of a process with a non-zero exit code. | `exited normally with exit code` | 1/1 | 11 |
| 92 | abnormal | Error messages related to missing components or unsupported operations | `not present and is stopping`, `unsupportedoperationexception:` | 1/1 | 2 |
| 93 | abnormal | Indicates an unhandled exception or unsupported operation in the system. | `unsupportedoperationexception:`, `java.lang.unsupportedoperationexception:` | 1/1 | 2 |
| 94 | abnormal | Log messages that contain 'NullPointerException' are abnormal. | `while inserting monitor info into db caught java.lang.nullpointerexception` | 1/1 | 2 |
| 95 | abnormal | Points to a failure during database operations or data insertion. | `db caught java.lang.nullpointerexception`, `inserting monitor info into db` | 1/1 | 2 |
| 96 | abnormal | Signals an uncorrectable error condition in the system's torus network. | `uncorrectable torus error` | 1/1 | 2 |
| 97 | abnormal | Signals an uncorrectable hardware error. | `uncorrectable torus error`, `torus error` | 1/1 | 2 |
| 98 | abnormal | Log messages that include 'power module status fault detected on node card' are abnormal. | `power module status fault detected on node card` | 1/1 | 1 |
| 99 | abnormal | Log messages mentioning 'power module status fault detected' indicate an abnormal condition. | `power module status fault detected` | 1/1 | 1 |
