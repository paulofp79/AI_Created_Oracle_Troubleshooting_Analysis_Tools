/**
 * Cell SQL Stat parser for Oracle Exadata ExaWatcher collector output.
 * Parses the fixed-width, two-line "cellsqlstat --detail --batch" format.
 */
(function attachCellSqlStatParser(global) {
    'use strict';

    const METRIC_DEFS = [
        { key: 'durationSeconds', label: 'Duration', kind: 'duration' },
        { key: 'memoryBytes', label: 'Memory Bytes', kind: 'bytes' },
        { key: 'cpuPercent', label: '%CPU', kind: 'percent' },
        { key: 'requestedBytes', label: 'Requested Bytes', kind: 'bytes' },
        { key: 'requestedBytesPerSec', label: 'Requested Bytes/s', kind: 'bytesPerSec' },
        { key: 'returnedBytes', label: 'Returned Bytes', kind: 'bytes' },
        { key: 'returnedBytesPerSec', label: 'Returned Bytes/s', kind: 'bytesPerSec' },
        { key: 'storageIndexPercent', label: '%Storage Index', kind: 'percent' },
        { key: 'xrmemColumnarPercent', label: '%XRMEM Columnar', kind: 'percent' },
        { key: 'flashColumnarPercent', label: '%Flash Columnar', kind: 'percent' },
        { key: 'flashRegularPercent', label: '%Flash Regular', kind: 'percent' },
        { key: 'diskPercent', label: '%Disk', kind: 'percent' },
        { key: 'passthruPercent', label: '%Passthru', kind: 'percent' },
        { key: 'xrmemColumnarBytes', label: 'XRMEM Columnar Bytes', kind: 'bytes' },
        { key: 'xrmemColumnarBytesPerSec', label: 'XRMEM Columnar Bytes/s', kind: 'bytesPerSec' },
        { key: 'flashColumnarBytes', label: 'Flash Columnar Bytes', kind: 'bytes' },
        { key: 'flashColumnarBytesPerSec', label: 'Flash Columnar Bytes/s', kind: 'bytesPerSec' },
        { key: 'flashRegularBytes', label: 'Flash Regular Bytes', kind: 'bytes' },
        { key: 'flashRegularBytesPerSec', label: 'Flash Regular Bytes/s', kind: 'bytesPerSec' },
        { key: 'diskBytes', label: 'Disk Bytes', kind: 'bytes' },
        { key: 'diskBytesPerSec', label: 'Disk Bytes/s', kind: 'bytesPerSec' },
        { key: 'columnarSavedBytes', label: 'Columnar Saved Bytes', kind: 'bytes' },
        { key: 'columnarSavedBytesPerSec', label: 'Columnar Saved Bytes/s', kind: 'bytesPerSec' },
        { key: 'storageIndexSavedBytes', label: 'Storage Index Saved Bytes', kind: 'bytes' },
        { key: 'storageIndexSavedBytesPerSec', label: 'Storage Index Saved Bytes/s', kind: 'bytesPerSec' },
        { key: 'passthruBytes', label: 'Passthru Bytes', kind: 'bytes' },
        { key: 'passthruBytesPerSec', label: 'Passthru Bytes/s', kind: 'bytesPerSec' },
        { key: 'ios', label: 'IOs', kind: 'count' },
        { key: 'iosPerSec', label: 'IOs/s', kind: 'countPerSec' },
        { key: 'requestReturnRatio', label: 'Returned / Requested', kind: 'ratio' },
        { key: 'offloadSavingsPercent', label: 'Offload Savings %', kind: 'percent' }
    ];

    const DEFAULT_METRIC_KEYS = [
        'memoryBytes',
        'cpuPercent',
        'requestedBytesPerSec',
        'returnedBytesPerSec',
        'requestedBytes',
        'returnedBytes',
        'storageIndexPercent',
        'xrmemColumnarPercent',
        'flashColumnarPercent',
        'flashRegularPercent',
        'diskPercent',
        'passthruPercent',
        'iosPerSec',
        'ios'
    ];

    const NUMERIC_FIELDS = METRIC_DEFS.map(def => def.key);

    const METRIC_MAP = METRIC_DEFS.reduce((acc, def) => {
        acc[def.key] = def;
        return acc;
    }, {});

    const SIZE_MULTIPLIERS = {
        K: 1024,
        M: 1024 ** 2,
        G: 1024 ** 3,
        T: 1024 ** 4,
        P: 1024 ** 5
    };

    function normalizeToken(value) {
        if (value == null) return null;
        const trimmed = String(value).trim();
        return trimmed.length ? trimmed : null;
    }

    function cleanLineForWidth(line) {
        return String(line || '')
            .replace(/\uFEFF/g, '')
            .replace(/[\u0001-\u0008\u000B\u000C\u000E-\u001F]/g, ' ');
    }

    function compactLineForSearch(line) {
        return cleanLineForWidth(line)
            .replace(/\u0000/g, '')
            .replace(/\t/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function parseHeader(lines) {
        const metadata = {};
        lines.forEach(line => {
            const raw = cleanLineForWidth(line);
            if (!raw.trimStart().startsWith('#')) return;
            const cleaned = raw.replace(/^#+\s*/, '');
            const separatorIndex = cleaned.indexOf(':');
            if (separatorIndex === -1) return;
            const key = cleaned.slice(0, separatorIndex).trim();
            const value = cleaned.slice(separatorIndex + 1).trim();
            if (key) metadata[key] = value;
        });
        return metadata;
    }

    function parseLegend(lines) {
        const startIndex = lines.findIndex(line => compactLineForSearch(line) === 'Column Name Legend');
        if (startIndex === -1) return {};

        const legend = {};
        for (let index = startIndex + 1; index < lines.length; index += 1) {
            const line = cleanLineForWidth(lines[index]);
            const searchLine = compactLineForSearch(line);
            if (!searchLine) {
                if (Object.keys(legend).length) break;
                continue;
            }
            if (/^-+$/.test(searchLine)) continue;
            if (searchLine.startsWith('Current Time:')) break;

            const match = line.match(/^(.+?)\s*:\s*(.+)$/);
            if (!match) continue;

            const key = match[1].trim();
            const value = match[2].trim();
            if (key) legend[key] = value;
        }
        return legend;
    }

    function parseTimestampValue(value) {
        const normalized = normalizeToken(value);
        if (!normalized) return null;

        const date = new Date(normalized.replace(' ', 'T'));
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function findColumnLayout(lines) {
        let separatorLine = null;

        for (let index = 0; index < lines.length; index += 1) {
            const searchLine = compactLineForSearch(lines[index]);
            const looksLikeHeader = (
                /\bCDBID\b/.test(searchLine) &&
                /\bDBID\b/.test(searchLine) &&
                /\bSQLID\b/.test(searchLine) &&
                /\bDBNAME\b/.test(searchLine)
            );

            if (!looksLikeHeader) continue;

            for (let offset = 1; offset <= 4; offset += 1) {
                const candidate = cleanLineForWidth(lines[index + offset] || '');
                if (isSeparatorLine(candidate)) {
                    separatorLine = candidate;
                    break;
                }
            }

            if (separatorLine) break;
        }

        if (!separatorLine) {
            throw new Error('Unable to locate the cellsqlstat fixed-width table header.');
        }

        const starts = [];
        let inDash = false;

        for (let index = 0; index < separatorLine.length; index += 1) {
            const isDash = separatorLine[index] === '-';
            if (isDash && !inDash) {
                starts.push(index);
                inDash = true;
            } else if (!isDash) {
                inDash = false;
            }
        }

        return starts.map((start, index) => ({
            start,
            end: starts[index + 1] || separatorLine.length
        }));
    }

    function sliceFixedWidth(line, layout) {
        const safeLine = cleanLineForWidth(line);
        return layout.map(range => normalizeToken(safeLine.slice(range.start, range.end)));
    }

    function isSeparatorLine(line) {
        const safeLine = cleanLineForWidth(line).replace(/\t/g, ' ');
        return Boolean(safeLine && /^\s*-+(?:\s+-+)+\s*$/.test(safeLine));
    }

    function looksLikeDataTop(tokens) {
        const sqlToken = (tokens[2] || '').toUpperCase();
        const dbNameToken = (tokens[22] || '').toUpperCase();

        if (sqlToken === 'SQLID' || dbNameToken === 'DBNAME') return false;

        return (
            /^\d+$/.test(tokens[0] || '') ||
            /^\d+$/.test(tokens[1] || '') ||
            Boolean(tokens[3]) ||
            sqlToken === 'OTHERS' ||
            sqlToken === 'TOTAL USAGE' ||
            dbNameToken === 'OTHERS' ||
            dbNameToken === 'TOTAL USAGE'
        );
    }

    function parseScaledValue(token) {
        const value = normalizeToken(token);
        if (!value || value === '-') return null;

        const cleaned = value.replace(/,/g, '').replace(/\/s$/i, '');
        const match = cleaned.match(/^([0-9]*\.?[0-9]+)([KMGTP])?$/i);
        if (match) {
            const base = parseFloat(match[1]);
            const suffix = (match[2] || '').toUpperCase();
            const multiplier = SIZE_MULTIPLIERS[suffix] || 1;
            return base * multiplier;
        }

        const numeric = Number(cleaned);
        return Number.isFinite(numeric) ? numeric : null;
    }

    function parseDurationSeconds(token) {
        const value = normalizeToken(token);
        if (!value || value === '-') return null;

        const match = value.match(/^(\d+):(\d+):(\d+(?:\.\d+)?)$/);
        if (!match) return null;

        const hours = Number(match[1]);
        const minutes = Number(match[2]);
        const seconds = Number(match[3]);
        return (hours * 3600) + (minutes * 60) + seconds;
    }

    function classifyRow(tokens) {
        const sqlToken = (tokens[2] || '').toUpperCase();
        const dbNameToken = (tokens[22] || '').toUpperCase();

        if (sqlToken === 'TOTAL USAGE' || dbNameToken === 'TOTAL USAGE') return 'total';
        if (sqlToken === 'OTHERS' || dbNameToken === 'OTHERS') return 'others';
        return 'sql';
    }

    function parseRowPair(topLine, bottomLine, layout) {
        const top = sliceFixedWidth(topLine, layout);
        if (!looksLikeDataTop(top)) return null;

        const bottom = sliceFixedWidth(bottomLine, layout);
        const rowCategory = classifyRow(top);

        const row = {
            rowCategory,
            cdbid: top[0],
            dbid: top[1],
            sqlId: rowCategory === 'sql'
                ? top[2]
                : (top[2] || (rowCategory === 'others' ? 'OTHERS' : 'TOTAL USAGE')),
            dbName: top[22] || (rowCategory === 'others' ? 'OTHERS' : rowCategory === 'total' ? 'TOTAL USAGE' : null),
            durationText: top[3],
            durationSeconds: parseDurationSeconds(top[3]),
            memoryBytes: parseScaledValue(top[4]),
            cpuPercent: parseScaledValue(bottom[5]),
            requestedBytes: parseScaledValue(top[6]),
            requestedBytesPerSec: parseScaledValue(bottom[6]),
            returnedBytes: parseScaledValue(top[7]),
            returnedBytesPerSec: parseScaledValue(bottom[7]),
            storageIndexPercent: parseScaledValue(bottom[8]),
            xrmemColumnarPercent: parseScaledValue(bottom[9]),
            flashColumnarPercent: parseScaledValue(bottom[10]),
            flashRegularPercent: parseScaledValue(bottom[11]),
            diskPercent: parseScaledValue(bottom[12]),
            passthruPercent: parseScaledValue(bottom[13]),
            xrmemColumnarBytes: parseScaledValue(top[14]),
            xrmemColumnarBytesPerSec: parseScaledValue(bottom[14]),
            flashColumnarBytes: parseScaledValue(top[15]),
            flashColumnarBytesPerSec: parseScaledValue(bottom[15]),
            flashRegularBytes: parseScaledValue(top[16]),
            flashRegularBytesPerSec: parseScaledValue(bottom[16]),
            diskBytes: parseScaledValue(top[17]),
            diskBytesPerSec: parseScaledValue(bottom[17]),
            columnarSavedBytes: parseScaledValue(top[18]),
            columnarSavedBytesPerSec: parseScaledValue(bottom[18]),
            storageIndexSavedBytes: parseScaledValue(top[19]),
            storageIndexSavedBytesPerSec: parseScaledValue(bottom[19]),
            passthruBytes: parseScaledValue(top[20]),
            passthruBytesPerSec: parseScaledValue(bottom[20]),
            ios: parseScaledValue(top[21]),
            iosPerSec: parseScaledValue(bottom[21])
        };

        row.requestReturnRatio = (
            row.requestedBytes != null &&
            row.returnedBytes != null &&
            row.requestedBytes > 0
        ) ? row.returnedBytes / row.requestedBytes : null;

        row.offloadSavingsPercent = row.requestReturnRatio != null
            ? Math.max(0, (1 - row.requestReturnRatio) * 100)
            : null;

        return row;
    }

    function mergeRows(rawRows) {
        const merged = new Map();

        rawRows.forEach(row => {
            const key = [
                row.timestampText,
                row.rowCategory,
                row.cdbid || '',
                row.dbid || '',
                row.sqlId || '',
                row.dbName || ''
            ].join('|');

            if (!merged.has(key)) {
                merged.set(key, {
                    ...row,
                    sections: row.section ? [row.section] : [],
                    sectionRanks: row.section && row.rankInSection != null
                        ? { [row.section]: row.rankInSection }
                        : {}
                });
                return;
            }

            const target = merged.get(key);

            if (row.section && !target.sections.includes(row.section)) {
                target.sections.push(row.section);
            }

            if (row.section && row.rankInSection != null) {
                target.sectionRanks[row.section] = row.rankInSection;
            }

            if (row.durationSeconds != null && (target.durationSeconds == null || row.durationSeconds > target.durationSeconds)) {
                target.durationSeconds = row.durationSeconds;
                target.durationText = row.durationText;
            }

            NUMERIC_FIELDS.forEach(field => {
                const nextValue = row[field];
                if (nextValue == null) return;
                if (target[field] == null || nextValue > target[field]) {
                    target[field] = nextValue;
                }
            });
        });

        return Array.from(merged.values()).sort((left, right) => {
            const leftTime = left.timestamp ? left.timestamp.getTime() : 0;
            const rightTime = right.timestamp ? right.timestamp.getTime() : 0;
            if (leftTime !== rightTime) return leftTime - rightTime;

            if (left.rowCategory !== right.rowCategory) {
                return left.rowCategory.localeCompare(right.rowCategory);
            }

            return (left.sqlId || '').localeCompare(right.sqlId || '');
        });
    }

    function parseRows(lines, layout) {
        const rawRows = [];
        let currentTimestampText = null;
        let currentTimestamp = null;
        let currentSection = null;
        let currentRank = 0;

        for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index];
            const searchLine = compactLineForSearch(line);

            if (searchLine.startsWith('Current Time:')) {
                currentTimestampText = searchLine.split(':').slice(1).join(':').trim();
                currentTimestamp = parseTimestampValue(currentTimestampText);
                currentSection = null;
                currentRank = 0;
                continue;
            }

            const sectionMatch = searchLine.match(/^Top SQL by (.+)$/);
            if (sectionMatch) {
                currentSection = sectionMatch[1].trim();
                currentRank = 0;
                continue;
            }

            if (!currentTimestampText || !currentSection) continue;
            if (!line.trim() || isSeparatorLine(line)) continue;

            const tokens = sliceFixedWidth(line, layout);
            if (!looksLikeDataTop(tokens)) continue;

            const row = parseRowPair(line, lines[index + 1] || '', layout);
            if (!row) continue;

            currentRank += 1;

            rawRows.push({
                ...row,
                timestampText: currentTimestampText,
                timestamp: currentTimestamp,
                section: currentSection,
                rankInSection: row.rowCategory === 'total' ? null : currentRank
            });

            index += 1;
        }

        return rawRows;
    }

    function uniqueSorted(values) {
        return Array.from(new Set(values.filter(Boolean))).sort((left, right) => {
            if (typeof left === 'number' && typeof right === 'number') return left - right;
            return String(left).localeCompare(String(right));
        });
    }

    function parseCellSqlStat(text) {
        const normalizedText = String(text || '')
            .replace(/\u0000/g, '')
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n');
        const lines = normalizedText.split('\n');

        const metadata = parseHeader(lines);
        const legend = parseLegend(lines);
        const layout = findColumnLayout(lines);
        const rawRows = parseRows(lines, layout);
        const records = mergeRows(rawRows);

        const timestamps = uniqueSorted(records.map(row => row.timestampText));
        const sqlRecords = records.filter(row => row.rowCategory === 'sql');

        return {
            metadata,
            legend,
            rawRows,
            records,
            sqlRecords,
            totals: records.filter(row => row.rowCategory === 'total'),
            others: records.filter(row => row.rowCategory === 'others'),
            timestamps,
            sections: uniqueSorted(rawRows.map(row => row.section)),
            cdbids: uniqueSorted(sqlRecords.map(row => row.cdbid)),
            dbids: uniqueSorted(sqlRecords.map(row => row.dbid)),
            dbNames: uniqueSorted(sqlRecords.map(row => row.dbName)),
            sqlIds: uniqueSorted(sqlRecords.map(row => row.sqlId)),
            snapshotCount: timestamps.length,
            metricDefinitions: METRIC_DEFS,
            defaultMetricKeys: DEFAULT_METRIC_KEYS
        };
    }

    function formatBinary(value) {
        if (value == null || Number.isNaN(value)) return '-';
        if (value === 0) return '0B';

        const units = ['B', 'K', 'M', 'G', 'T', 'P'];
        let size = Math.abs(value);
        let unitIndex = 0;

        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex += 1;
        }

        const digits = size >= 100 ? 0 : size >= 10 ? 1 : 2;
        const prefix = value < 0 ? '-' : '';
        return `${prefix}${size.toFixed(digits)}${units[unitIndex]}`;
    }

    function formatCount(value, decimals = 0) {
        if (value == null || Number.isNaN(value)) return '-';
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    function formatDuration(value) {
        if (value == null || Number.isNaN(value)) return '-';

        const totalSeconds = Math.floor(value);
        const hundredths = Math.round((value - totalSeconds) * 100);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        return [
            String(hours).padStart(2, '0'),
            String(minutes).padStart(2, '0'),
            `${String(seconds).padStart(2, '0')}.${String(hundredths).padStart(2, '0')}`
        ].join(':');
    }

    function formatMetricValue(metricKey, value) {
        const metric = METRIC_MAP[metricKey];
        if (!metric) return value == null ? '-' : String(value);
        if (value == null || Number.isNaN(value)) return '-';

        switch (metric.kind) {
            case 'bytes':
                return formatBinary(value);
            case 'bytesPerSec':
                return `${formatBinary(value)}/s`;
            case 'percent':
                return `${Number(value).toLocaleString(undefined, {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: value >= 100 ? 0 : 2
                })}%`;
            case 'count':
                return formatCount(value, Number.isInteger(value) ? 0 : 1);
            case 'countPerSec':
                return `${formatCount(value, Number.isInteger(value) ? 0 : 1)}/s`;
            case 'duration':
                return formatDuration(value);
            case 'ratio':
                return Number(value).toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                });
            default:
                return String(value);
        }
    }

    function metricValue(record, key) {
        const value = record ? record[key] : null;
        return Number.isFinite(value) ? value : null;
    }

    const api = {
        METRIC_DEFS,
        DEFAULT_METRIC_KEYS,
        parseCellSqlStat,
        formatMetricValue,
        formatBinary,
        formatCount,
        formatDuration,
        metricValue
    };

    global.CellSqlStatParser = api;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
}(typeof window !== 'undefined' ? window : globalThis));
