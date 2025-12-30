/**
 * Oracle Exadata Troubleshooting Tools - Common Utilities
 * Shared functions for all HTML-based analysis tools
 * Author: Paulo Portugal - Oracle XTeam
 */

// ============================================
// HTML Escaping (XSS Prevention)
// ============================================

/**
 * Escape HTML special characters to prevent XSS attacks
 * @param {string} text - Raw text that may contain HTML
 * @returns {string} - Escaped safe text
 */
function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Escape text and then apply syntax highlighting
 * Safe for displaying log content with highlighted keywords
 * @param {string} text - Raw log text
 * @returns {string} - Escaped and highlighted HTML
 */
function escapeAndHighlight(text) {
    let safe = escapeHtml(text);
    // Apply highlighting after escaping
    safe = safe.replace(/(ORA-\d+)/g, '<span class="ora">$1</span>');
    safe = safe.replace(/(ERROR)/gi, '<span class="error">$1</span>');
    safe = safe.replace(/(FATAL)/gi, '<span class="fatal">$1</span>');
    safe = safe.replace(/(FAIL)/gi, '<span class="fail">$1</span>');
    safe = safe.replace(/(WARNING)/gi, '<span class="warning">$1</span>');
    return safe;
}

// ============================================
// Tab Management
// ============================================

/**
 * Initialize tab switching functionality
 * @param {string} tabNavSelector - CSS selector for tab navigation container
 * @param {string} tabContentSelector - CSS selector for tab content containers
 */
function initTabs(tabNavSelector, tabContentSelector) {
    const tabs = document.querySelectorAll(`${tabNavSelector} [data-tab]`);
    const contents = document.querySelectorAll(tabContentSelector);

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetId = tab.dataset.tab;

            // Update tab buttons
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Update content panels
            contents.forEach(c => {
                c.classList.toggle('active', c.id === targetId);
            });
        });
    });
}

/**
 * Switch to a specific tab by name
 * @param {string} tabName - The data-tab value to switch to
 */
function switchTab(tabName) {
    const tab = document.querySelector(`[data-tab="${tabName}"]`);
    if (tab) tab.click();
}

// ============================================
// Loading Indicators
// ============================================

/**
 * Show a loading overlay
 * @param {string} message - Loading message to display
 * @param {string} containerId - Optional container ID (defaults to body)
 */
function showLoading(message = 'Loading...', containerId = null) {
    // Remove existing loader if present
    hideLoading();

    const loader = document.createElement('div');
    loader.id = 'loading-overlay';
    loader.innerHTML = `
        <div class="loading-spinner"></div>
        <div class="loading-message">${escapeHtml(message)}</div>
    `;
    loader.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        z-index: 9999;
        color: #fff;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    `;

    // Add spinner CSS if not already present
    if (!document.getElementById('loading-styles')) {
        const style = document.createElement('style');
        style.id = 'loading-styles';
        style.textContent = `
            .loading-spinner {
                width: 50px;
                height: 50px;
                border: 4px solid rgba(255, 255, 255, 0.3);
                border-top-color: #00bfff;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            }
            .loading-message {
                margin-top: 16px;
                font-size: 16px;
            }
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
        `;
        document.head.appendChild(style);
    }

    const container = containerId ? document.getElementById(containerId) : document.body;
    container.appendChild(loader);
}

/**
 * Hide the loading overlay
 */
function hideLoading() {
    const loader = document.getElementById('loading-overlay');
    if (loader) loader.remove();
}

/**
 * Update the loading message
 * @param {string} message - New message to display
 */
function updateLoadingMessage(message) {
    const msgEl = document.querySelector('#loading-overlay .loading-message');
    if (msgEl) msgEl.textContent = message;
}

// ============================================
// File Handling
// ============================================

/**
 * Validate file type against allowed extensions
 * @param {File} file - File object to validate
 * @param {string[]} allowedExtensions - Array of allowed extensions (e.g., ['.txt', '.log'])
 * @returns {boolean} - True if file type is valid
 */
function validateFileType(file, allowedExtensions) {
    if (!file || !file.name) return false;
    const fileName = file.name.toLowerCase();
    return allowedExtensions.some(ext => fileName.endsWith(ext.toLowerCase()));
}

/**
 * Format file size for display
 * @param {number} bytes - File size in bytes
 * @returns {string} - Human-readable file size
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

/**
 * Read file as text with error handling
 * @param {File} file - File to read
 * @returns {Promise<string>} - File contents
 */
async function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`));
        reader.readAsText(file);
    });
}

/**
 * Read file as ArrayBuffer with error handling
 * @param {File} file - File to read
 * @returns {Promise<ArrayBuffer>} - File contents as ArrayBuffer
 */
async function readFileAsArrayBuffer(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`));
        reader.readAsArrayBuffer(file);
    });
}

// ============================================
// CSV Export
// ============================================

/**
 * Convert array of objects to CSV string
 * @param {Object[]} data - Array of data objects
 * @param {string[]} columns - Column names (keys from objects)
 * @returns {string} - CSV formatted string
 */
function arrayToCsv(data, columns) {
    if (!data || !data.length) return '';

    const headers = columns || Object.keys(data[0]);
    const csvRows = [headers.join(',')];

    for (const row of data) {
        const values = headers.map(header => {
            const val = row[header];
            // Escape quotes and wrap in quotes if contains comma or newline
            if (typeof val === 'string' && (val.includes(',') || val.includes('\n') || val.includes('"'))) {
                return `"${val.replace(/"/g, '""')}"`;
            }
            return val ?? '';
        });
        csvRows.push(values.join(','));
    }

    return csvRows.join('\n');
}

/**
 * Download data as a CSV file
 * @param {string} csvContent - CSV formatted string
 * @param {string} filename - Name for the downloaded file
 */
function downloadCsv(csvContent, filename = 'export.csv') {
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

// ============================================
// Date/Time Utilities
// ============================================

/**
 * Parse various timestamp formats
 * @param {string} text - Text that may contain a timestamp
 * @returns {Date|null} - Parsed Date object or null
 */
function parseTimestamp(text) {
    if (!text) return null;

    // Try ISO 8601 format: 2025-08-05T10:00:00
    const isoMatch = text.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
    if (isoMatch) return new Date(isoMatch[1]);

    // Try Oracle alert log format: Mon Aug 05 10:00:00 2025
    const alertMatch = text.match(/(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})/);
    if (alertMatch) return new Date(alertMatch[1]);

    // Try common format: 2025/08/05 10:00:00
    const commonMatch = text.match(/(\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2})/);
    if (commonMatch) return new Date(commonMatch[1].replace(/\//g, '-'));

    return null;
}

/**
 * Format date for display
 * @param {Date} date - Date object
 * @param {boolean} includeTime - Whether to include time
 * @returns {string} - Formatted date string
 */
function formatDate(date, includeTime = true) {
    if (!date || !(date instanceof Date) || isNaN(date)) return '';

    const options = {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    };

    if (includeTime) {
        options.hour = '2-digit';
        options.minute = '2-digit';
        options.second = '2-digit';
    }

    return date.toLocaleString(undefined, options);
}

// ============================================
// Chart Utilities
// ============================================

/**
 * Generate an array of distinct colors for chart series
 * @param {number} count - Number of colors needed
 * @returns {string[]} - Array of hex color codes
 */
function generateChartColors(count) {
    const baseColors = [
        '#00bfff', '#ff5050', '#66ff66', '#ffd966', '#ff66cc',
        '#a366ff', '#ffaa00', '#00ffaa', '#ff6b6b', '#4ecdc4',
        '#45b7d1', '#96ceb4', '#ffeaa7', '#dfe6e9', '#fd79a8'
    ];

    const colors = [];
    for (let i = 0; i < count; i++) {
        colors.push(baseColors[i % baseColors.length]);
    }
    return colors;
}

/**
 * Safely destroy a Chart.js chart instance
 * @param {Chart} chart - Chart instance to destroy
 */
function destroyChart(chart) {
    if (chart && typeof chart.destroy === 'function') {
        chart.destroy();
    }
}

// ============================================
// Error Handling
// ============================================

/**
 * Display an error message to the user
 * @param {string} message - Error message
 * @param {string} containerId - Optional container ID for inline error display
 */
function showError(message, containerId = null) {
    if (containerId) {
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = `<div class="error-message" style="color: #ff5050; padding: 10px; background: #2a1a1a; border-radius: 8px; border-left: 4px solid #ff5050;">${escapeHtml(message)}</div>`;
            return;
        }
    }
    alert(message);
}

/**
 * Log error with context for debugging
 * @param {string} context - Where the error occurred
 * @param {Error} error - Error object
 */
function logError(context, error) {
    console.error(`[${context}]`, error);
}

// ============================================
// Export for module usage (if needed)
// ============================================

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        escapeHtml,
        escapeAndHighlight,
        initTabs,
        switchTab,
        showLoading,
        hideLoading,
        updateLoadingMessage,
        validateFileType,
        formatFileSize,
        readFileAsText,
        readFileAsArrayBuffer,
        arrayToCsv,
        downloadCsv,
        parseTimestamp,
        formatDate,
        generateChartColors,
        destroyChart,
        showError,
        logError
    };
}
