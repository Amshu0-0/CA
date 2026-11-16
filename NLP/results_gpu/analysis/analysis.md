# Error analysis (test period)

## Recall per alert category (window level)

| category | n_windows | logbert | logbert_full |
|---|---|---|---|
| APPSEV | 341 | 0.977 | 0.997 |
| KERNRTSP | 154 | 0.994 | 0.994 |
| KERNMC | 141 | 0.979 | 0.986 |
| MASABNORM | 61 | 0.984 | 0.984 |
| KERNMNTF | 31 | 0.968 | 1.000 |
| KERNSOCK | 23 | 0.391 | 0.435 |
| KERNDTLB | 9 | 1.000 | 1.000 |
| KERNCON | 8 | 0.875 | 1.000 |
| APPOUT | 7 | 0.714 | 0.714 |
| LINKIAP | 7 | 0.143 | 0.429 |
| KERNREC | 4 | 0.750 | 1.000 |
| MMCS | 4 | 1.000 | 1.000 |
| KERNSERV | 4 | 1.000 | 1.000 |
| KERNTLBE | 4 | 1.000 | 1.000 |
| LINKBLL | 3 | 1.000 | 1.000 |
| KERNSTOR | 2 | 0.500 | 1.000 |
| KERNRTSA | 1 | 0.000 | 0.000 |

## Agreement on anomalous windows

| method_a | method_b | both_caught | only_a | only_b | neither | shared_false_positives |
|---|---|---|---|---|---|---|
| logbert | logbert_full | 456 | 0 | 15 | 24 | 2126 |

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

