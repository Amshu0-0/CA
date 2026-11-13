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
| APPSEV | 341 | 0.977 | 0.953 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNRTSP | 154 | 0.994 | 0.994 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNMC | 141 | 0.979 | 0.986 | 1.000 | 1.000 | 1.000 | 0.979 |
| MASABNORM | 61 | 0.984 | 0.984 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNMNTF | 31 | 0.968 | 0.935 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNSOCK | 23 | 0.391 | 0.217 | 1.000 | 0.174 | 0.087 | 0.000 |
| KERNDTLB | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| KERNCON | 8 | 0.875 | 0.875 | 1.000 | 1.000 | 1.000 | 1.000 |
| APPOUT | 7 | 0.714 | 0.714 | 1.000 | 0.714 | 0.714 | 0.714 |
| LINKIAP | 7 | 0.143 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 |
| KERNREC | 4 | 0.750 | 0.750 | 1.000 | 1.000 | 0.000 | 0.000 |
| MMCS | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.750 |
| KERNSERV | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.750 |
| KERNTLBE | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| LINKBLL | 3 | 1.000 | 0.667 | 1.000 | 0.000 | 1.000 | 0.000 |
| KERNSTOR | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 0.500 | 0.500 |
| KERNRTSA | 1 | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Agreement on anomalous windows

| method_a | method_b | both_caught | only_a | only_b | neither | shared_false_positives |
|---|---|---|---|---|---|---|
| logbert | logbert_full | 442 | 14 | 2 | 37 | 1925 |
| logbert | llm_zero_shot | 456 | 0 | 39 | 0 | 1792 |
| logbert | llm_few_shot | 448 | 8 | 18 | 21 | 312 |
| logbert | llm_rule_based | 446 | 10 | 21 | 18 | 208 |
| logbert | keyword_rules | 438 | 18 | 15 | 24 | 0 |
| logbert_full | llm_zero_shot | 444 | 0 | 51 | 0 | 1671 |
| logbert_full | llm_few_shot | 440 | 4 | 26 | 25 | 284 |
| logbert_full | llm_rule_based | 436 | 8 | 31 | 20 | 193 |
| logbert_full | keyword_rules | 430 | 14 | 23 | 28 | 0 |
| llm_zero_shot | llm_few_shot | 466 | 29 | 0 | 0 | 819 |
| llm_zero_shot | llm_rule_based | 467 | 28 | 0 | 0 | 488 |
| llm_zero_shot | keyword_rules | 453 | 42 | 0 | 0 | 0 |
| llm_few_shot | llm_rule_based | 457 | 9 | 10 | 19 | 488 |
| llm_few_shot | keyword_rules | 453 | 13 | 0 | 29 | 0 |
| llm_rule_based | keyword_rules | 453 | 14 | 0 | 28 | 0 |

## Combining Approach A and Approach B

A window is flagged if either method flags it (OR) or only if both do (AND).

| method_a | method_b | combination | precision | recall | f1 | fp |
|---|---|---|---|---|---|---|
| logbert | llm_zero_shot | either (OR) | 0.100 | 1.000 | 0.181 | 4470 |
| logbert | llm_zero_shot | both (AND) | 0.203 | 0.921 | 0.332 | 1792 |
| logbert | llm_few_shot | either (OR) | 0.144 | 0.958 | 0.250 | 2823 |
| logbert | llm_few_shot | both (AND) | 0.589 | 0.905 | 0.714 | 312 |
| logbert | llm_rule_based | either (OR) | 0.164 | 0.964 | 0.280 | 2439 |
| logbert | llm_rule_based | both (AND) | 0.682 | 0.901 | 0.776 | 208 |
| logbert | keyword_rules | either (OR) | 0.179 | 0.952 | 0.301 | 2159 |
| logbert | keyword_rules | both (AND) | 1.000 | 0.885 | 0.939 | 0 |
| logbert_full | llm_zero_shot | either (OR) | 0.101 | 1.000 | 0.183 | 4407 |
| logbert_full | llm_zero_shot | both (AND) | 0.210 | 0.897 | 0.340 | 1671 |
| logbert_full | llm_few_shot | either (OR) | 0.150 | 0.949 | 0.259 | 2667 |
| logbert_full | llm_few_shot | both (AND) | 0.608 | 0.889 | 0.722 | 284 |
| logbert_full | llm_rule_based | either (OR) | 0.173 | 0.960 | 0.293 | 2270 |
| logbert_full | llm_rule_based | both (AND) | 0.693 | 0.881 | 0.776 | 193 |
| logbert_full | keyword_rules | either (OR) | 0.191 | 0.943 | 0.318 | 1975 |
| logbert_full | keyword_rules | both (AND) | 1.000 | 0.869 | 0.930 | 0 |

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
| only llm_few_shot | 18 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only llm_few_shot | 18 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only llm_few_shot | 18 | 34 | data <*> <*> | 1 |
| only llm_few_shot | 18 | 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| only llm_few_shot | 18 | 206 | <*> <*> <*> socket closed | 1 |
| neither | 21 | 206 | <*> <*> <*> socket closed | 13 |
| neither | 21 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| neither | 21 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### A LogBERT vs B3 LLM rule-based

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only LogBERT | 10 | 206 | <*> <*> <*> socket closed | 7 |
| only LogBERT | 10 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 3 |
| only llm_rule_based | 21 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only llm_rule_based | 21 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| only llm_rule_based | 21 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only llm_rule_based | 21 | 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| only llm_rule_based | 21 | 258 | <*> exited abnormally due to signal: Aborted | 1 |
| neither | 18 | 206 | <*> <*> <*> socket closed | 14 |
| neither | 18 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| neither | 18 | 34 | data <*> <*> | 1 |
| neither | 18 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 |

### A LogBERT vs B4 Keyword rules (no LLM)

| caught_by | group_windows | template_id | template | windows |
|---|---|---|---|---|
| only LogBERT | 18 | 206 | <*> <*> <*> socket closed | 9 |
| only LogBERT | 18 | 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 3 |
| only LogBERT | 18 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 3 |
| only LogBERT | 18 | 86 | DDR <*> <*> register: <*> <*> | 2 |
| only LogBERT | 18 | 475 | L<*> major internal error | 1 |
| only keyword_rules | 15 | 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 8 |
| only keyword_rules | 15 | 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 3 |
| only keyword_rules | 15 | 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| only keyword_rules | 15 | 258 | <*> exited abnormally due to signal: Aborted | 1 |
| only keyword_rules | 15 | 320 | Lustre mount FAILED : bglio<*> : block_id : location | 1 |
| neither | 24 | 206 | <*> <*> <*> socket closed | 14 |
| neither | 24 | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 6 |
| neither | 24 | 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| neither | 24 | 34 | data <*> <*> | 1 |
| neither | 24 | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 |

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
| 206 | <*> <*> <*> socket closed | 18 |
| 573 | ciod: Error reading message prefix on CioStream socket to <*>:<*>, <*> <*> <*> <*> | 16 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 7 |
| 320 | Lustre mount FAILED : bglio<*> : block_id : location | 2 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 631 | machine check interrupt (bit=<*>): L<*> dcache unit <*> <*> parity error | 2 |
| 281 | <*> <*> <*> BGLERR_IDO_PKT_TIMEOUT connection lost to node/link/service card | 1 |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 1 |
| 258 | <*> exited abnormally due to signal: Aborted | 1 |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 |

### B1 LLM zero-shot

_(none)_

### B2 LLM few-shot (case-based)

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 19 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 7 |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 3 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |

### B3 LLM rule-based

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 21 |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 4 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 34 | data <*> <*> | 1 |

### B4 Keyword rules (no LLM)

| template_id | template | missed_windows |
|---|---|---|
| 206 | <*> <*> <*> socket closed | 23 |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 7 |
| 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 4 |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 3 |
| 86 | DDR <*> <*> register: <*> <*> | 2 |
| 65 | ciod: LOGIN <*> failed: <*> <*> | 2 |
| 34 | data <*> <*> | 1 |
| 475 | L<*> major internal error | 1 |
| 571 | Power Good signal deactivated: <*> A service action may be required. | 1 |
| 20 | machine <*> <*> | 1 |

## Misclassified templates (Approach B)

### B4 Keyword rules (no LLM)

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | no rule fired |
| false negative | 20 | machine <*> <*> | 127 | 4 |  | no rule fired |
| false negative | 34 | data <*> <*> | 79 | 1 |  | no rule fired |
| false negative | 86 | DDR <*> <*> register: <*> <*> | 52 | 26 |  | no rule fired |
| false negative | 206 | <*> <*> <*> socket closed | 37 | 37 |  | no rule fired |
| false negative | 571 | Power Good signal deactivated: <*> A service action may be required. | 15 | 15 |  | no rule fired |
| false negative | 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | 8 | 8 |  | no rule fired |
| false negative | 475 | L<*> major internal error | 2 | 2 |  | no rule fired |
| false negative | 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | 2 | 2 |  | no rule fired |
| false negative | 480 | Error receiving packet on tree network, <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*>) PSR<*>=<*>f<*> PSR<*>=<*> PRXF=<*> PIXF=<*> | 1 | 1 |  | no rule fired |
| false negative | 851 | rts assertion failed: `vaddr % PAGE_SIZE_<*>M == <*>' in `int initializeAppMemory(int, TLBEntry&, unsigned int, unsigned int)' at mmu.cc:<*> | 1 | 1 |  | no rule fired |

### B2 LLM few-shot (case-based)

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | 27510 | 0 |  | Signal reception often indicates an issue. |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 |  | Error loading with permission denied suggests a failure. |
| false positive | 342 | ciod: generated <*> core files for program <*> | 6062 | 0 |  | Log mentions generation of core files which usually indicates a crash or error. |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 |  | Mentioned debugger died |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 |  | Error loading invalid or missing program image |
| false positive | 16 | ciod: LOGIN <*> failed: No such file or directory | 3623 | 0 |  | The log indicates a failure to find a file, which is an error condition. |
| false positive | 568 | fpr<*>=<*> ffffffff <*> <*> | 1329 | 0 |  | The log message contains an error or unexpected state indicated by 'fpr' and 'fffffff'. |
| false positive | 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | 1024 | 0 |  | Error message indicates a failure to load a program image. |
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | The log message indicates a login attempt failure, which is a normal operational event. |
| false positive | 114 | Node card VPD check: U<*> node in processor card slot J<*> do not match. VPD ecid <*> found <*> | 658 | 0 |  | VPD check failure indicates a mismatch that needs attention. |
| false positive | 111 | Node card is not fully functional | 585 | 0 |  | The message indicates a hardware issue. |
| false positive | 112 | Can not get assembly information for node card | 585 | 0 |  | The message indicates a failure to get assembly information, which is unusual. |
| false positive | 113 | Node card status: no ALERTs are active. Clock Mode is Low. Clock Select is Midplane. Phy JTAG Reset is asserted. ASIC JTAG Reset is <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> <*> OK. MPGOOD ERROR LATCH IS ACTIVE. The <*>.<*> volt rail is OK. The <*>.<*> volt rail is OK. | 583 | 0 |  | MPGOOD ERROR LATCH IS ACTIVE |
| false positive | 555 | invalid <*> | 390 | 0 |  | Unrecognized or invalid parameters are typically errors. |
| false positive | 187 | ciod: Error loading <*> program image too big, <*> > <*> | 208 | 0 |  | Error messages indicate a problem. |

### B3 LLM rule-based

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | 27510 | 0 | 8.0 | contains 'terminated' |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 | 4.0 | contains 'Error loading' and permission denied |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 | 11.0 | Signals system or hardware issues that require attention. |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 | 4.0 | Error loading program image indicates a failure |
| false positive | 16 | ciod: LOGIN <*> failed: No such file or directory | 3623 | 0 | 4.0 | indicates a system error or failure |
| false positive | 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | 1024 | 0 | 25.0 | mentions 'mount failed' |
| false negative | 65 | ciod: LOGIN <*> failed: <*> <*> | 816 | 816 |  | No specific keyword phrases trigger an abnormal condition. |
| false positive | 187 | ciod: Error loading <*> program image too big, <*> > <*> | 208 | 0 | 4.0 | program image too big |
| false positive | 77 | capture first <*> <*> error address..<*> | 156 | 0 | 4.0 | contains 'terminated for reason' |
| false negative | 20 | machine <*> <*> | 127 | 4 | 30.0 | Describes a state or condition without indicating an error. |
| false positive | 80 | <*> error detected in EDRAM bank <*> | 104 | 0 | 4.0 | contains 'error detected' which is indicative of a system failure |
| false negative | 34 | data <*> <*> | 79 | 1 | 1.0 | Describes normal system operations or status updates. |
| false positive | 81 | parity error in <*> <*> | 78 | 0 | 24.0 | mentions 'parity error' which is indicative of a hardware issue |
| false positive | 538 | Machine Check Status Register: <*> | 53 | 0 | 3.0 | contains 'machine check interrupt' |
| false positive | 27 | imprecise machine check...........<*> | 53 | 0 | 4.0 | contains 'machine check interrupt' |

### B1 LLM zero-shot

| error | template_id | template | n_test | n_test_anomalous | rule | reason |
|---|---|---|---|---|---|---|
| false positive | 750 | <*> total interrupts. <*> critical input interrupts. <*> microseconds total spent on critical input interrupts, <*> microseconds max time in a critical input interrupt. | 135092 | 0 |  | High critical input interrupts suggest a potential issue. |
| false positive | 1 | instruction cache parity error corrected | 33547 | 0 |  | instruction cache parity error indicates a hardware issue |
| false positive | 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | 27510 | 0 |  | Received signal indicates an unexpected event |
| false positive | 760 | Kernel detected <*> integer alignment exceptions (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> (<*>) iar <*>, dear <*> | 22175 | 0 |  | Integer alignment exceptions are typically errors |
| false positive | 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | 13952 | 0 |  | Permission denied error indicates a failure to load the program image. |
| false positive | 59 | ciod: pollControlDescriptors: Detected the debugger died. | 5603 | 0 |  | Debugger death indicates a failure |
| false positive | 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | 4752 | 0 |  | Invalid or missing program image |
| false positive | 16 | ciod: LOGIN <*> failed: No such file or directory | 3623 | 0 |  | The login attempt failed due to a missing file. |
| false positive | 529 | critical input interrupt (unit=<*> bit=<*>): warning for <*> <*> wire, suppressing further interrupts of same type | 3288 | 0 |  | Critical input interrupt suggests a failure |
| false positive | 530 | <*> tree receiver <*> in re-synch state event(s) (dcr <*>) detected over <*> seconds | 3003 | 0 |  | Resynchronization events indicate potential issues. |
| false positive | 568 | fpr<*>=<*> ffffffff <*> <*> | 1329 | 0 |  | FPR write failure |
| false positive | 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | 1024 | 0 |  | The log indicates an error loading a program image. |
| false positive | 114 | Node card VPD check: U<*> node in processor card slot J<*> do not match. VPD ecid <*> found <*> | 658 | 0 |  | Mismatch in VPD ecid indicates a failure. |
| false positive | 112 | Can not get assembly information for node card | 585 | 0 |  | Failed to retrieve assembly info |
| false positive | 111 | Node card is not fully functional | 585 | 0 |  | Node card not fully functional indicates a failure |

## Does the rule-based LLM apply the rules it cites?

- templates: 472
- answers citing a rule: 420
- cited rule occurs in template: 20
- cited rule label agrees: 418
- cited rule is keyword rule: 12
- abnormal answers: 37
- abnormal answers backed by an occurring rule: 7
- abnormal where keywords say normal: 28
- abnormal where keywords say normal with alerts: 5
- normal where keywords say abnormal: 0

Templates the LLM labels abnormal although no keyword rule does:

| template_id | template | has_alert | n_messages | cited_rule | cited_rule_occurs | reason |
|---|---|---|---|---|---|---|
| 569 | ciod: Received signal <*>, code=<*>, errno=<*>, address=<*> | False | 27510 | 8 | False | contains 'terminated' |
| 341 | ciod: Error loading <*> invalid or missing program image, Permission denied | False | 13952 | 4 | False | contains 'Error loading' and permission denied |
| 59 | ciod: pollControlDescriptors: Detected the debugger died. | False | 5603 | 11 | False | Signals system or hardware issues that require attention. |
| 10 | ciod: Error loading <*> invalid or missing program image, No such file or directory | False | 4752 | 4 | False | Error loading program image indicates a failure |
| 16 | ciod: LOGIN <*> failed: No such file or directory | False | 3623 | 4 | False | indicates a system error or failure |
| 806 | ciod: Error loading /p/gb<*>/light<*>/sppm_chkpt/sppm: not a CNK program image | False | 1024 | 25 | False | mentions 'mount failed' |
| 187 | ciod: Error loading <*> program image too big, <*> > <*> | False | 208 | 4 | False | program image too big |
| 77 | capture first <*> <*> error address..<*> | False | 156 | 4 | False | contains 'terminated for reason' |
| 80 | <*> error detected in EDRAM bank <*> | False | 104 | 4 | False | contains 'error detected' which is indicative of a system failure |
| 81 | parity error in <*> <*> | False | 78 | 24 | False | mentions 'parity error' which is indicative of a hardware issue |
| 27 | imprecise machine check...........<*> | False | 53 | 4 | False | contains 'machine check interrupt' |
| 24 | tlb error.........................<*> | False | 53 | 24 | False | contains 'tlb error interrupt' |
| 538 | Machine Check Status Register: <*> | False | 53 | 3 | False | contains 'machine check interrupt' |
| 104 | DDR failing data registers: <*> <*> | False | 26 | 4 | False | contains 'terminated for reason' |
| 93 | memory manager / command manager address parity..<*> | False | 26 | 16 | False | contains 'parityAlignment' |
| 100 | uncorrectable error...<*> | False | 26 | 4 | False | uncorrectable error |
| 717 | ciod: Error creating node map from file my_map.txt: No such file or directory | False | 24 | 4 | False | Error creating node map from file |
| 571 | Power Good signal deactivated: <*> A service action may be required. | True | 15 | 26 | False | power deactivated |
| 55 | rts internal error | False | 12 | 7 | False | indicates a critical system failure |
| 218 | ciod: Error <*> node map <*> <*> <*> <*> <*> <*> <*> | False | 8 | 11 | False | Signals system or hardware issues that require attention. |
| 179 | ciod: Unexpected eof at line <*> of node map file <*> | False | 8 | 14 | False | indicates abnormal termination |
| 328 | MidplaneSwitchController::receiveTrain() iap failed: R<*>-M<*>-L<*>-U<*>-A, <*> ec<*> | True | 8 | 11 | False | machine check interrupt |
| 259 | ciod: Error loading <*> invalid or missing program image, <*> <*> <*> | False | 8 | 4 | False | contains 'Error loading' and invalid image |
| 475 | L<*> major internal error | True | 2 | 4 | False | contains 'terminated for reason' |
| 716 | MidplaneSwitchController::clearPort() bll_clear_port failed: R<*>-M<*>-L<*>-U<*>-A | True | 2 | 11 | False | machine check interrupt or power module status fault detected |
| 115 | ciod: sendMsgToDebugger: error sending PROGRAM_EXITED message to debugger. | False | 1 | 9 | False | contains 'terminated' |
| 795 | fpr<*>=<*> <*>e<*>e<*>f<*> <*>e<*>ebb<*> <*>e<*>e<*> | False | 1 | 4 | False | contains 'terminated for reason' |
| 851 | rts assertion failed: `vaddr % PAGE_SIZE_<*>M == <*>' in `int initializeAppMemory(int, TLBEntry&, unsigned int, unsigned int)' at mmu.cc:<*> | True | 1 | 16 | False | contains 'rts: bad message header' |

## Keyword rules: sensitivity to the number of rules

The reported runs use the top 30 of 99 verified rules. Choose k on the validation columns; the test columns are for reporting only.

| k | val_precision | val_recall | val_f1 | val_fp | test_precision | test_recall | test_f1 | test_fp |
|---|---|---|---|---|---|---|---|---|
| 5 | 1.000 | 0.469 | 0.638 | 0 | 1.000 | 0.784 | 0.879 | 0 |
| 10 | 1.000 | 0.695 | 0.820 | 0 | 1.000 | 0.891 | 0.942 | 0 |
| 15 | 1.000 | 0.711 | 0.831 | 0 | 1.000 | 0.891 | 0.942 | 0 |
| 20 | 1.000 | 0.711 | 0.831 | 0 | 1.000 | 0.891 | 0.942 | 0 |
| 25 | 1.000 | 0.711 | 0.831 | 0 | 1.000 | 0.899 | 0.947 | 0 |
| 30 | 1.000 | 0.720 | 0.837 | 0 | 1.000 | 0.915 | 0.956 | 0 |
| 35 | 1.000 | 0.794 | 0.885 | 0 | 1.000 | 0.962 | 0.980 | 0 |
| 40 | 1.000 | 0.794 | 0.885 | 0 | 1.000 | 0.962 | 0.980 | 0 |
| 45 | 1.000 | 0.798 | 0.888 | 0 | 1.000 | 0.962 | 0.980 | 0 |
| 50 | 1.000 | 0.798 | 0.888 | 0 | 1.000 | 0.962 | 0.980 | 0 |
| 60 | 0.942 | 0.807 | 0.870 | 22 | 1.000 | 0.962 | 0.980 | 0 |
| 80 | 0.942 | 0.807 | 0.870 | 22 | 1.000 | 0.976 | 0.988 | 0 |
| 99 | 0.942 | 0.807 | 0.870 | 22 | 1.000 | 0.976 | 0.988 | 0 |

