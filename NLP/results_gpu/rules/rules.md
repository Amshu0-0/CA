| # | Label | Rule | Keywords | Train templates (correct/matched) | Train messages |
|---:|---|---|---|---:|---:|
| 1 | normal | Indicates a normal operational message | `fee` | 25/25 | 33 |
| 2 | normal | System status alerts | `alert`, `clock mode`, `temperature mask`, `temperature limit error latch`, `temperature error`, `pgood error latch`, `mpgood error latch` | 7/7 | 16,196 |
| 3 | abnormal | Signals a kernel panic, a serious system error. | `kernel`, `panic` | 6/6 | 21,054 |
| 4 | abnormal | System log entries related to abnormal termination | `exited abnormally`, `terminated for reason` | 6/6 | 17,205 |
| 5 | abnormal | Signals a critical system error. | `rts panic!`, `rts: kernel terminated` | 5/5 | 21,042 |
| 6 | abnormal | Indicates termination of kernel due to unexpected reasons. | `kernel`, `terminated`, `reason` | 5/5 | 17,199 |
| 7 | abnormal | Indicates termination of the kernel. | `kernel`, `terminated` | 5/5 | 17,199 |
| 8 | abnormal | Kernel terminations and panics are severe errors. | `kernel terminated for reason`, `kernel panic` | 5/5 | 17,199 |
| 9 | abnormal | Reflects a failure in the communication or network infrastructure. | `no ethernet link`, `socket to`, `socket closed` | 4/4 | 25,882 |
| 10 | abnormal | Signals a kernel termination due to an error. | `rts: kernel terminated` | 4/4 | 17,187 |
| 11 | abnormal | Signals a termination due to an invalid CPU in the message header. | `rts: kernel terminated`, `invalid cpu` | 4/4 | 17,187 |
| 12 | abnormal | FileSystem mounting failure | `lustre mount failed`, `error: unable to mount filesystem` | 3/3 | 28,272 |
| 13 | abnormal | Network and communication issues | `ethernet link`, `ciostream socket`, `vdp ecid` | 3/3 | 25,765 |
| 14 | abnormal | Indicates a failure in the initialization or setup phase of the system. | `monitor caught`, `microloader assertion`, `rts panic!` | 3/3 | 5,360 |
| 15 | abnormal | Lustre mount failures are critical system errors. | `lustre mount failed` | 2/2 | 27,552 |
| 16 | abnormal | Signals a critical error in the Lustre filesystem | `lustre mount`, `fs error` | 2/2 | 27,552 |
| 17 | abnormal | Signals an error in reading message prefix. | `error reading message prefix`, `failed to read message prefix` | 2/2 | 25,756 |
| 18 | abnormal | Signals a communication failure | `failed to read message prefix`, `bglerr_ido_pkt_timeout` | 2/2 | 5,995 |
| 19 | abnormal | Signals a failure in the execution flow or program logic. | `stopping execution`, `assertion` | 2/2 | 5,358 |
| 20 | abnormal | Signals a critical system error | `rts panic!`, `monitor caught java.lang.unsupportedoperationexception` | 2/2 | 3,857 |
| 21 | abnormal | Indicates a power issue. | `power deactivated`, `power module status fault detected` | 2/2 | 193 |
| 22 | abnormal | Points to a machine check interrupt related to cache unit data parity error. | `machine check interrupt`, `dcache unit data parity error` | 2/2 | 80 |
| 23 | abnormal | Machine check interrupt suggests hardware failure | `machine check interrupt` | 2/2 | 80 |
| 24 | abnormal | Machine check interrupts due to hardware errors | `machine check interrupt`, `l dcache unit data parity error` | 2/2 | 80 |
| 25 | abnormal | Parity errors in communication | `parityalignment`, `data parity` | 2/2 | 46 |
| 26 | abnormal | Signals a failure in the midplane switch controller. | `midplaneswitchcontroller::parityalignment`, `midplaneswitchcontroller::sendtrain` | 2/2 | 38 |
| 27 | normal | General informational messages about system state | `nodecard temperature sensor chip`, `this link card`, `data plb`, `added subnets and addresses to db` | 2/2 | 27 |
| 28 | abnormal | Abnormal exits due to signals suggest process termination problems. | `exited abnormally due to signal`, `aborted` | 2/2 | 18 |
| 29 | abnormal | Abnormal exits with signals like 'Abort' or 'Segmentation fault' are errors. | `exited abnormally due to signal: aborted`, `exited abnormally due to signal: segmentation fault` | 2/2 | 18 |
| 30 | abnormal | Denotes a system or process exiting abnormally. | `exited abnormally`, `segmentation fault` | 2/2 | 18 |
| 31 | abnormal | Highlights a TLB error interrupt. | `tlb error interrupt` | 1/1 | 152,723 |
| 32 | normal | Represents a warning or informational message about a recoverable issue. | `input pipe error(s) detected and corrected`, `instruction cache parity error corrected` | 1/1 | 67,171 |
| 33 | normal | Indicates a service action being performed. | `as part of service action` | 1/1 | 2,600 |
| 34 | abnormal | Signals a microloader assertion failure | `microloader assertion` | 1/1 | 1,503 |
| 35 | abnormal | Signals a microloader assertion failure. | `microloader`, `assertion` | 1/1 | 1,503 |
| 36 | abnormal | Indicates a power deactivation event. | `power deactivated` | 1/1 | 192 |
| 37 | abnormal | Signals a power issue. | `power deactivated:` | 1/1 | 192 |
| 38 | normal | Job timeout | `timed out` | 1/1 | 186 |
| 39 | abnormal | Indicates a failure in receiving a train | `iap failed`, `midplaneswitchcontroller::receivetrain` | 1/1 | 158 |
| 40 | abnormal | Midplane switch controller failures are critical. | `midplaneswitchcontroller::receivetrain() iap failed` | 1/1 | 158 |
| 41 | abnormal | Signals a critical error in the system's midplane switch controller | `midplaneswitchcontroller::receivetrain` | 1/1 | 158 |
| 42 | abnormal | Socket closure indicates a network issue | `socket closed` | 1/1 | 117 |
| 43 | abnormal | Signals a midplane switch controller failure. | `midplaneswitchcontroller::parityalignment() pap failed:` | 1/1 | 14 |
| 44 | abnormal | Signals a connection issue. | `connection lost`, `bglerr_ido_pkt_timeout` | 1/1 | 12 |
| 45 | abnormal | Signals a kernel panic | `kernel panic` | 1/1 | 12 |
| 46 | abnormal | Connection loss events | `connection lost` | 1/1 | 12 |
| 47 | abnormal | Indicates a connection loss | `bglerr_ido_pkt_timeout connection lost to node/link/service card` | 1/1 | 12 |
| 48 | abnormal | Signals a connection loss. | `connection lost to node/link/service card` | 1/1 | 12 |
| 49 | abnormal | Signals a segmentation fault | `segmentation fault` | 1/1 | 3 |
| 50 | abnormal | Signals a segmentation fault | `segmentation fault`, `mmcs_server exited abnormally due to signal: segmentation fault` | 1/1 | 3 |
| 51 | abnormal | Signals an unsupported operation. | `unsupportedoperationexception` | 1/1 | 2 |
| 52 | abnormal | Indicates an unsupported operation or missing component. | `unsupportedoperationexception`, `not present` | 1/1 | 2 |
| 53 | abnormal | Signals a hardware error. | `torus error` | 1/1 | 2 |
| 54 | abnormal | Power module status faults are serious hardware errors. | `power module status fault detected` | 1/1 | 1 |
