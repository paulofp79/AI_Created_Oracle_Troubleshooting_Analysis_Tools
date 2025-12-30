# Sample Data Files

This directory contains example input files for testing the Oracle Exadata Troubleshooting Tools.

## Expected File Formats

### For ECS_Analysis.py

**File:** `sample_ecstat.dat`

```
# ECStatJSONExaWatcher collected data
# Sample Interval(s): 5
# Collection started: 2025-08-05 10:00:00
zzz <2025-08-05T10:00:00.000-0000>
{
  "celldisk stats": [
    {
      "timestampFormatted": "2025-08-05 10:00:00",
      "intendedDeviceType": "FD",
      "stats": {
        "reads": {"iops": 1000, "bytes": 4096000},
        "writes": {"iops": 500, "bytes": 2048000}
      }
    }
  ]
}
zzz <2025-08-05T10:00:05.000-0000>
...
```

### For RDS_Info_Analysis.html

**File:** `sample_rds.dat`

```
send_lock_contention 12345
send_lock_queue_raced 678
cong_update_queued 910
cong_update_received 1112
cong_send_error 0
ib_tx_ring_full 50
ib_tx_stalled 25
```

### For CPU_Charts_From_ATP_Files.html

**File:** `CPUManager_sample.zip` containing `CPUManager_*.json`

```json
{
  "statNames": ["timestamp", "%usr", "%nice", "%sys", "%idle", "%wio", "%irq", "%softirq", "%steal"],
  "values": [
    [1725523200000, 25.5, 0.0, 10.2, 60.3, 2.0, 0.5, 0.5, 1.0],
    [1725523260000, 30.2, 0.0, 12.1, 55.7, 1.5, 0.3, 0.2, 0.0]
  ]
}
```

### For vmstat_multi_plot.html

**File:** `sample_vmstat.txt`

```
# Starting Time: 2025/08/05 10:00:00
procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
10:00:00  1  0      0 8000000 200000 4000000    0    0    10    20 1000 2000 25 10 60  5  0
10:00:01  2  0      0 7950000 200000 4000000    0    0    15    25 1100 2100 28 12 55  5  0
```

### For alertlog_analyzer.html

**File:** `alert_DBNAME.log`

```
2025-08-05T10:00:00.000000+00:00
Starting ORACLE instance (normal) (OS id: 12345)
2025-08-05T10:00:05.000000+00:00
ORA-00600: internal error code, arguments: [kghstack_underflow_internal_3]
2025-08-05T10:00:10.000000+00:00
ERROR: failed to open file
```

### For Exa_Cell_Metrics_Chart.html

**File:** `sample_metrics.lst` (tab-separated)

```
1	FC_IO_RQ_R_MISS_SEC	cell01	150.5	2025-08-05 10:00:00
2	FC_IO_RQ_R_MISS_SEC	cell01	145.2	2025-08-05 10:01:00
3	FC_IO_BY_W_POPULATE_SEC	cell01	200.0	2025-08-05 10:00:00
```

## Notes

- All sample files should be placed in this directory
- Files can be used to verify tool functionality
- Timestamps should be adjusted to match your testing timeframe
