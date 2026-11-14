# Error analysis (test period)

## Template label purity

3 of 472 test templates are mixed (hold both normal and alert messages); they contain 258 of 482,331 test messages and 31 of 28,915 alert messages.

Best possible template-level classifier (each template labeled by its test majority): message-level F1 = 0.999, window-level F1 = 0.996.

| template_id | template | n_test | n_test_anomalous | alert_tags |
|---|---|---|---|---|
| 20 | machine <*> <*> | 127 | 4 | KERNMC |
| 34 | data <*> <*> | 79 | 1 | KERNSTOR |
| 86 | DDR <*> <*> register: <*> <*> | 52 | 26 | KERNMC |

## Templates whose label changes between training and test

The majority label in the training period differs from the one in the test period. Methods that learn template labels from training data cannot get these right.

| template_id | template | n_train | n_train_anomalous | n_test | n_test_anomalous |
|---|---|---|---|---|---|
| 65 | ciod: LOGIN <*> failed: <*> <*> | 472 | 0 | 816 | 816 |
| 34 | data <*> <*> | 84360 | 63488 | 79 | 1 |

## Recall per alert category (window level)

| category | n_windows | logbert | logbert_full | llm_zero_shot | llm_few_shot | llm_rule_based | keyword_rules |
|---|---|---|---|---|---|---|---|
| APPSEV | 341 | 0.977 | 0.997 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNRTSP | 154 | 0.994 | 0.994 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNMC | 141 | 0.979 | 0.986 | 1.000 | 1.000 | 1.000 | 0.979 |
| MASABNORM | 61 | 0.984 | 0.984 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNMNTF | 31 | 0.968 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNSOCK | 23 | 0.391 | 0.435 | 1.000 | 0.130 | 1.000 | 1.000 |
| KERNDTLB | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.778 |
| KERNCON | 8 | 0.875 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| APPOUT | 7 | 0.714 | 0.714 | 1.000 | 0.714 | 0.714 | 0.714 |
| LINKIAP | 7 | 0.143 | 0.429 | 1.000 | 0.000 | 1.000 | 0.000 |
| KERNREC | 4 | 0.750 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| MMCS | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.750 |
| KERNSERV | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.750 |
| KERNTLBE | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 | 0.500 |
| LINKBLL | 3 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0.667 |
| KERNSTOR | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 |
| KERNRTSA | 1 | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Agreement on anomalous windows

| method_a | method_b | both_caught | only_a | only_b | neither | shared_false_positives |
|---|---|---|---|---|---|---|
| logbert | logbert_full | 456 | 0 | 15 | 24 | 2126 |
| logbert | llm_zero_shot | 456 | 0 | 39 | 0 | 1819 |
| logbert | llm_few_shot | 448 | 8 | 17 | 22 | 141 |
| logbert | llm_rule_based | 454 | 2 | 37 | 2 | 236 |
| logbert | keyword_rules | 443 | 13 | 29 | 10 | 59 |
| logbert_full | llm_zero_shot | 471 | 0 | 24 | 0 | 2047 |
| logbert_full | llm_few_shot | 460 | 11 | 5 | 19 | 158 |
| logbert_full | llm_rule_based | 469 | 2 | 22 | 2 | 253 |
| logbert_full | keyword_rules | 454 | 17 | 18 | 6 | 59 |
| llm_zero_shot | llm_few_shot | 465 | 30 | 0 | 0 | 595 |
| llm_zero_shot | llm_rule_based | 491 | 4 | 0 | 0 | 690 |
| llm_zero_shot | keyword_rules | 472 | 23 | 0 | 0 | 59 |
| llm_few_shot | llm_rule_based | 463 | 2 | 28 | 2 | 507 |
| llm_few_shot | keyword_rules | 452 | 13 | 20 | 10 | 49 |
| llm_rule_based | keyword_rules | 472 | 19 | 0 | 4 | 0 |

## Combining Approach A and Approach B

A window is flagged if either method flags it (OR) or only if both do (AND).

| method_a | method_b | combination | precision | recall | f1 | fp |
|---|---|---|---|---|---|---|
| logbert | llm_zero_shot | either (OR) | 0.098 | 1.000 | 0.179 | 4549 |
| logbert | llm_zero_shot | both (AND) | 0.200 | 0.921 | 0.329 | 1819 |
| logbert | llm_few_shot | either (OR) | 0.154 | 0.956 | 0.265 | 2603 |
| logbert | llm_few_shot | both (AND) | 0.761 | 0.905 | 0.827 | 141 |
| logbert | llm_rule_based | either (OR) | 0.159 | 0.996 | 0.275 | 2603 |
| logbert | llm_rule_based | both (AND) | 0.658 | 0.917 | 0.766 | 236 |
| logbert | keyword_rules | either (OR) | 0.184 | 0.980 | 0.310 | 2149 |
| logbert | keyword_rules | both (AND) | 0.882 | 0.895 | 0.889 | 59 |
| logbert_full | llm_zero_shot | either (OR) | 0.097 | 1.000 | 0.177 | 4617 |
| logbert_full | llm_zero_shot | both (AND) | 0.187 | 0.952 | 0.313 | 2047 |
| logbert_full | llm_few_shot | either (OR) | 0.142 | 0.962 | 0.247 | 2882 |
| logbert_full | llm_few_shot | both (AND) | 0.744 | 0.929 | 0.827 | 158 |
| logbert_full | llm_rule_based | either (OR) | 0.146 | 0.996 | 0.255 | 2882 |
| logbert_full | llm_rule_based | both (AND) | 0.650 | 0.947 | 0.771 | 253 |
| logbert_full | keyword_rules | either (OR) | 0.167 | 0.988 | 0.285 | 2445 |
| logbert_full | keyword_rules | both (AND) | 0.885 | 0.917 | 0.901 | 59 |

## Anomalous windows only one approach catches

### A LogBERT vs B1 LLM zero-shot

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only llm_zero_shot | 39 | 206 | <*> <*> <*> socket closed | 14 |
| only llm_zero_shot | 39 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only llm_zero_shot | 39 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| only llm_zero_shot | 39 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only llm_zero_shot | 39 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### A LogBERT vs B2 LLM few-shot (case-based)

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only LogBERT | 8 | 206 | <*> <*> <*> socket closed | 6 |
| only LogBERT | 8 | 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 3 |
| only LogBERT | 8 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 1 |
| only llm_few_shot | 17 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only llm_few_shot | 17 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only llm_few_shot | 17 | 34 | data <*> <*> | 1 |
| only llm_few_shot | 17 | 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| only llm_few_shot | 17 | 258 | <*> exited abnormally due to signal: Aborted | 1 |
| neither | 22 | 206 | <*> <*> <*> socket closed | 14 |
| neither | 22 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| neither | 22 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### A LogBERT vs B3 LLM rule-based

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only LogBERT | 2 | 18 | <*> TLB error interrupt | 2 |
| only llm_rule_based | 37 | 206 | <*> <*> <*> socket closed | 14 |
| only llm_rule_based | 37 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only llm_rule_based | 37 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| only llm_rule_based | 37 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only llm_rule_based | 37 | 34 | data <*> <*> | 1 |
| neither | 2 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### A LogBERT vs B4 Keyword rules (no LLM)

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only LogBERT | 13 | 18 | <*> TLB error interrupt | 4 |
| only LogBERT | 13 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 3 |
| only LogBERT | 13 | 86 | DDR <*> <*> register: <*> <*> | 2 |
| only LogBERT | 13 | 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 1 |
| only LogBERT | 13 | 475 | L<*> major internal error | 1 |
| only keyword_rules | 29 | 206 | <*> <*> <*> socket closed | 14 |
| only keyword_rules | 29 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only keyword_rules | 29 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only keyword_rules | 29 | 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| only keyword_rules | 29 | 258 | <*> exited abnormally due to signal: Aborted | 1 |
| neither | 10 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| neither | 10 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| neither | 10 | 34 | data <*> <*> | 1 |
| neither | 10 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 |

## Alert templates in missed windows

### A  LogBERT

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 14 |
| 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 34 | data <*> <*> | 1 |
| 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| 258 | <*> exited abnormally due to signal: Aborted | 1 |
| 320 | Lustre mount FAILED : bglio<*> : block_id : location | 1 |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 |

### A+ LogBERT, full-coverage masking (ext.)

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 13 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 4 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 2 |
| 258 | <*> exited abnormally due to signal: Aborted | 1 |
| 48 | rts panic! - stopping execution | 1 |
| 851 | rts assertion failed: `vaddr % PAGE_SIZE_<*>M == <*>' in `int initializeAppMemory(int, TLBEntry&, unsigned int, unsigned int)' at mmu.cc:<*> | 1 |
| 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 1 |

### B1 LLM zero-shot

_(none)_

### B2 LLM few-shot (case-based)

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 20 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 7 |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 3 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### B3 LLM rule-based

| template_id | template | missed_windows |
|---|---|---|
| 18 | <*> TLB error interrupt | 2 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### B4 Keyword rules (no LLM)

| template_id | template | missed_windows |
|---|---|---|
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 7 |
| 18 | <*> TLB error interrupt | 4 |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 4 |
| 86 | DDR <*> <*> register: <*> <*> | 2 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 34 | data <*> <*> | 1 |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 1 |
| 475 | L<*> major internal error | 1 |
| 571 | Power Good signal deactivated: <*> A service action may be required. | 1 |
| 20 | machine <*> <*> | 1 |

## Misclassified templates (Approach B)

### B4 Keyword rules (no LLM)

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 760 | Kernel detected <*> integer alignment exceptions (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> | 22175 | 0 | 3.0 | kernel |
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | no rule fired |
| false negative | 20 | machine <*> <*> | 127 | 4 |  | no rule fired |
| false negative | 34 | data <*> <*> | 79 | 1 |  | no rule fired |
| false negative | 86 | DDR <*> <*> register: <*> <*> | 52 | 26 |  | no rule fired |
| false negative | 571 | Power Good signal deactivated: <*> A service action may be required. | 15 | 15 |  | no rule fired |
| false negative | 18 | <*> TLB error interrupt | 12 | 12 |  | no rule fired |
| false negative | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 8 | 8 |  | no rule fired |
| false positive | 751 | instance of a correctable ddr. RAS KERNEL INFO <*> microseconds spent in the rbs signal handler during <*> calls. <*> microseconds was the maximum time for a single instance of a correctable ddr. | 4 | 0 | 3.0 | kernel |
| false positive | 752 | max time in a cr RAS KERNEL INFO <*> total interrupts. <*> critical input interrupts. <*> microseconds total spent on critical input interrupts, <*> microseconds max time in a critical input interrupt. | 4 | 0 | 3.0 | kernel |
| false negative | 475 | L<*> major internal error | 2 | 2 |  | no rule fired |
| false negative | 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 2 | 2 |  | no rule fired |
| false positive | 805 | dear <*> (<*> RAS KERNEL INFO Kernel detected <*> integer alignment exceptions (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> | 2 | 0 | 3.0 | kernel |
| false negative | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 | 1 |  | no rule fired |

### B2 LLM few-shot (case-based)

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 760 | Kernel detected <*> integer alignment exceptions (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> | 22175 | 0 |  | Multiple integer alignment exceptions suggest potential hardware or software issues. |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 |  | Permission denied suggests a security issue or misconfiguration. |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 |  | Debugger death typically indicates an issue. |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 |  | Error loading with invalid file path suggests a potential anomaly. |
| false positive | 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | 1024 | 0 |  | Error message indicates a problem with loading a program image |
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | Login failure messages are common and do not necessarily indicate an anomaly. |
| false positive | 114 | Node card VPD check: U<*> node in processor card slot J<*> do not match. VPD ecid <*> found <*> | 658 | 0 |  | Mismatch between node card VPD and expected values |
| false positive | 112 | Can not get assembly information for node card | 585 | 0 |  | The message indicates a failure to retrieve necessary information, which is unusual. |
| false positive | 111 | Node card is not fully functional | 585 | 0 |  | Node card not fully functional is an unusual condition. |
| false positive | 113 | Node card status: no ALERTs are active. Clock Mode is Low. Clock Select is Midplane. Phy JTAG Reset is asserted. ASIC JTAG Reset is <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> OK. MPGOOD ERROR LATCH IS ACTIVE. The <*>.<*> volt rail is OK. The <*>.<*> volt rail is OK. | 583 | 0 |  | MPGOOD ERROR LATCH IS ACTIVE |
| false positive | 555 | invalid <*> | 390 | 0 |  | Unusual keyword suggests a problem |
| false positive | 187 | ciod: Error loading <*> program image too big, <*> > <*> | 208 | 0 |  | Program image size exceeded limit |
| false positive | 550 | invalid operation exception <*> | 195 | 0 |  | Invalid operation exceptions are typically errors that indicate a problem. |
| false positive | 77 | capture first <*> <*> error address..<*> | 156 | 0 |  | It mentions an error and an address, which could indicate a problem. |
| false positive | 558 | minus <*> | 130 | 0 |  | Unrecognized command or error message. |

### B3 LLM rule-based

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | 27510 | 0 | 30.0 | exited abnormally |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 | 30.0 | exited abnormally |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 | 30.0 | Detected the debugger died suggests an abnormal exit |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 | 30.0 | exited abnormally |
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | Login failure is not necessarily an abnormal condition. |
| false positive | 113 | Node card status: no ALERTs are active. Clock Mode is Low. Clock Select is Midplane. Phy JTAG Reset is asserted. ASIC JTAG Reset is <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> OK. MPGOOD ERROR LATCH IS ACTIVE. The <*>.<*> volt rail is OK. The <*>.<*> volt rail is OK. | 583 | 0 | 3.0 | mpgood error latch is active |
| false positive | 555 | invalid <*> | 390 | 0 | 11.0 | rts: kernel terminated |
| false positive | 187 | ciod: Error loading <*> program image too big, <*> > <*> | 208 | 0 | 30.0 | exited abnormally |
| false positive | 550 | invalid operation exception <*> | 195 | 0 | 20.0 | rts panic! and invalid operation exception indicate a critical system error |
| false positive | 77 | capture first <*> <*> error address..<*> | 156 | 0 | 30.0 | exited abnormally |
| false negative | 20 | machine <*> <*> | 127 | 4 |  | No specific keywords match the given log message. |
| false positive | 26 | d-cache <*> parity <*> | 106 | 0 | 25.0 | Parity errors in communication |
| false positive | 280 | Ido packet timeout | 92 | 0 | 18.0 | bglerr_ido_pkt_timeout |
| false positive | 25 | <*> parity <*> | 79 | 0 | 25.0 | Parity errors in communication |
| false negative | 34 | data <*> <*> | 79 | 1 |  | No specific keywords match the log message. |

### B1 LLM zero-shot

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 1 | instruction cache parity error corrected | 33547 | 0 |  | instruction cache parity error is an error condition |
| false positive | 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | 27510 | 0 |  | Received signal indicates an unexpected event |
| false positive | 760 | Kernel detected <*> integer alignment exceptions (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> | 22175 | 0 |  | Multiple integer alignment exceptions occurred |
| false positive | 6 | CE sym <*>, at <*>, mask <*> | 19837 | 0 |  | CE sym indicates a communication error between nodes |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 |  | Permission denied for loading program image |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 |  | Debugger death indicates a potential issue. |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 |  | Invalid or missing program image |
| false positive | 16 | ciod: LOGIN <*> failed: No such file or directory | 3623 | 0 |  | The login attempt failed due to a non-existent file. |
| false positive | 529 | critical input interrupt (unit=<*> bit=<*>): warning for <*> <*> wire, suppressing further interrupts of same type | 3288 | 0 |  | critical input interrupt suggests a potential hardware or software issue |
| false positive | 530 | <*> tree receiver <*> in re-synch state event(s) (dcr <*>) detected over <*> seconds | 3003 | 0 |  | resynchronization events indicate potential issues |
| false positive | 568 | fpr<*>=<*> ffffffff <*> <*> | 1329 | 0 |  | fpr* values being set to ffffffff indicate an abnormal condition. |
| false positive | 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | 1024 | 0 |  | The log indicates an error in loading a checkpoint file, suggesting a failure. |
| false positive | 114 | Node card VPD check: U<*> node in processor card slot J<*> do not match. VPD ecid <*> found <*> | 658 | 0 |  | Mismatch between expected and actual node IDs |
| false positive | 112 | Can not get assembly information for node card | 585 | 0 |  | Failed to retrieve assembly information |
| false positive | 111 | Node card is not fully functional | 585 | 0 |  | Node card not fully functional indicates a hardware issue |

## Does the rule-based LLM apply the rules it cites?

- templates: 472
- answers citing a rule: 391
- cited rule occurs in template: 13
- cited rule label agrees: 390
- cited rule is keyword rule: 11
- abnormal answers: 47
- abnormal answers backed by an occurring rule: 9
- abnormal where keywords say normal: 37
- abnormal where keywords say normal with alerts: 5
- normal where keywords say abnormal: 4

Templates the LLM labels abnormal although no keyword rule does:

| template_id | template | has_alert | n_messages | cited_rule | cited_rule_occurs | reason |
|---|---|---|---|---|---|---|
| 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | False | 27510 | 30 | False | exited abnormally |
| 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | False | 13952 | 30 | False | exited abnormally |
| 59 | ciod: pollControlDescriptors: Detected the debugger died. | False | 5603 | 30 | False | Detected the debugger died suggests an abnormal exit |
| 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | False | 4752 | 30 | False | exited abnormally |
| 113 | Node card status: no ALERTs are active. Clock Mode is Low. Clock Select is Midplane. Phy JTAG Reset is asserted. ASIC JTAG Reset is <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> OK. MPGOOD ERROR LATCH IS ACTIVE. The <*>.<*> volt rail is OK. The <*>.<*> volt rail is OK. | False | 583 | 3 | False | mpgood error latch is active |
| 555 | invalid <*> | False | 390 | 11 | False | rts: kernel terminated |
| 187 | ciod: Error loading <*> program image too big, <*> > <*> | False | 208 | 30 | False | exited abnormally |
| 550 | invalid operation exception <*> | False | 195 | 20 | False | rts panic! and invalid operation exception indicate a critical system error |
| 77 | capture first <*> <*> error address..<*> | False | 156 | 30 | False | exited abnormally |
| 26 | d-cache <*> parity <*> | False | 106 | 25 | False | Parity errors in communication |
| 280 | Ido packet timeout | False | 92 | 18 | False | bglerr_ido_pkt_timeout |
| 25 | <*> parity <*> | False | 79 | 25 | False | Parity errors in communication |
| 81 | parity error in <*> <*> | False | 78 | 25 | False | parity errors in communication |
| 82 | parity error in read queue <*> | False | 78 | 25 | False | Parity errors in communication |
| 552 | underflow exception......................<*> | False | 65 | 30 | False | denotes a system or process exiting abnormally |
| 563 | invalid operation exception (int cnvt)...<*> | False | 65 | 20 | False | rts panic! and invalid operation exception suggest critical error |
| 553 | divide-by-zero exception.................<*> | False | 65 | 30 | False | denotes a system or process exiting abnormally |
| 554 | inexact exception........................<*> | False | 65 | 30 | False | exited abnormally |
| 449 | data cache <*> parity error detected. attempting to correct | False | 55 | 25 | False | parity errors in communication |
| 27 | imprecise machine check...........<*> | False | 53 | 22 | False | machine check interrupt suggests hardware failure |
| 24 | tlb error.........................<*> | False | 53 | 8 | False | tlb error is a kernel termination due to an error |
| 538 | Machine Check Status Register: <*> | False | 53 | 22 | False | machine check interrupt suggests hardware failure |
| 93 | memory manager / command manager address parity..<*> | False | 26 | 27 | False | general informational messages about system state |
| 99 | miscompare............<*> | False | 26 | 30 | False | exited abnormally |
| 104 | DDR failing data registers: <*> <*> | False | 26 | 30 | False | Denotes a system or process exiting abnormally |
| 100 | uncorrectable error...<*> | False | 26 | 8 | False | Kernel terminations and panics are severe errors |
| 85 | number of lines with parity errors written to L<*> EDRAMs...<*> | False | 26 | 25 | False | Parity errors in communication |
| 142 | program interrupt: <*> <*> | False | 24 | 8 | False | kernel terminated |
| 571 | Power Good signal deactivated: <*> A service action may be required. | True | 15 | 21 | False | power deactivated |
| 55 | rts internal error | False | 12 | 8 | False | rts internal error indicates a kernel termination due to an error |
| 259 | ciod: Error loading <*> invalid or missing program image, <*> <*> <*> | False | 8 | 30 | False | exited abnormally |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | True | 8 | 13 | False | network and communication issues |
| 610 | Exception Syndrome Register: <*> | False | 4 | 8 | False | kernel terminated for reason |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | True | 2 | 13 | False | network and communication issues |
| 475 | L<*> major internal error | True | 2 | 3 | False | major internal error |
| 115 | ciod: sendMsgToDebugger: error sending PROGRAM_EXITED message to debugger. | False | 1 | 30 | False | exited abnormally |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | True | 1 | 30 | False | Error receiving packet on tree network suggests a communication failure |

## Keyword rules: sensitivity to the number of rules

The reported runs use the top 30 of 54 verified rules. Choose k on the validation columns; the test columns are for reporting only.

| k | val_precision | val_recall | val_f1 | val_fp | test_precision | test_recall | test_f1 | test_fp |
|---|---|---|---|---|---|---|---|---|
| 5 | 1.000 | 0.372 | 0.542 | 0 | 0.785 | 0.434 | 0.559 | 59 |
| 10 | 1.000 | 0.623 | 0.768 | 0 | 0.879 | 0.863 | 0.871 | 59 |
| 15 | 0.933 | 0.682 | 0.788 | 22 | 0.885 | 0.917 | 0.901 | 59 |
| 20 | 0.933 | 0.691 | 0.794 | 22 | 0.887 | 0.933 | 0.909 | 59 |
| 25 | 0.941 | 0.785 | 0.856 | 22 | 0.889 | 0.954 | 0.920 | 59 |
| 30 | 0.941 | 0.785 | 0.856 | 22 | 0.889 | 0.954 | 0.920 | 59 |
| 35 | 0.941 | 0.785 | 0.856 | 22 | 0.890 | 0.962 | 0.924 | 59 |
| 40 | 0.941 | 0.785 | 0.856 | 22 | 0.891 | 0.976 | 0.932 | 59 |
| 45 | 0.941 | 0.785 | 0.856 | 22 | 0.891 | 0.976 | 0.932 | 59 |
| 50 | 0.941 | 0.785 | 0.856 | 22 | 0.891 | 0.976 | 0.932 | 59 |
| 54 | 0.942 | 0.803 | 0.867 | 22 | 0.891 | 0.976 | 0.932 | 59 |

