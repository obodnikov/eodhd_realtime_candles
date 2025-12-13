/**
 * Admin Panel JavaScript
 * Handles interactive features and AJAX requests
 */

/**
 * Show alert message
 * @param {string} message - Alert message
 * @param {string} category - Alert category (success, danger, warning, info)
 */
function showAlert(message, category = 'info') {
    const container = document.querySelector('main .container') || document.querySelector('.container');

    if (!container) {
        console.error('Cannot show alert: container not found');
        return;
    }

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${category}`;
    alertDiv.innerHTML = `
        ${message}
        <button class="alert-close" onclick="this.parentElement.remove()">&times;</button>
    `;

    // Insert at the top of the container
    container.insertBefore(alertDiv, container.firstChild);

    // Auto-remove after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}

/**
 * Format number with specified decimals
 * @param {number} value - Number to format
 * @param {number} decimals - Number of decimal places
 * @returns {string} Formatted number
 */
function formatNumber(value, decimals = 2) {
    try {
        return parseFloat(value).toFixed(decimals);
    } catch {
        return value;
    }
}

/**
 * Format timestamp to readable date
 * @param {number|string} timestamp - Unix timestamp or ISO string
 * @returns {string} Formatted date string
 */
function formatDateTime(timestamp) {
    try {
        const date = typeof timestamp === 'number'
            ? new Date(timestamp * 1000)
            : new Date(timestamp);

        return date.toLocaleString();
    } catch {
        return timestamp;
    }
}

/**
 * Confirm action with custom message
 * @param {string} message - Confirmation message
 * @returns {boolean} User confirmation
 */
function confirmAction(message) {
    return confirm(message);
}

/**
 * Debounce function to limit rate of function calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Auto-refresh page status
 * Can be enabled on dashboard pages
 */
let autoRefreshInterval = null;

function enableAutoRefresh(intervalSeconds = 30) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }

    autoRefreshInterval = setInterval(() => {
        location.reload();
    }, intervalSeconds * 1000);

    console.log(`Auto-refresh enabled: every ${intervalSeconds} seconds`);
}

function disableAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
        console.log('Auto-refresh disabled');
    }
}

/**
 * Handle form validation
 * @param {HTMLFormElement} form - Form element
 * @returns {boolean} Validation result
 */
function validateForm(form) {
    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');

    for (const input of inputs) {
        if (!input.value.trim()) {
            showAlert(`Please fill in: ${input.labels[0]?.textContent || 'required field'}`, 'warning');
            input.focus();
            return false;
        }
    }

    return true;
}

/**
 * Copy text to clipboard
 * @param {string} text - Text to copy
 */
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showAlert('Copied to clipboard', 'success');
    } catch (error) {
        console.error('Failed to copy:', error);
        showAlert('Failed to copy to clipboard', 'danger');
    }
}

/**
 * Initialize page
 */
document.addEventListener('DOMContentLoaded', function() {
    console.log('Admin panel initialized');

    // Add loading indicator for forms
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function(e) {
            const submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn && !submitBtn.disabled) {
                const originalText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.textContent = 'Processing...';

                // Re-enable after 5 seconds as fallback
                setTimeout(() => {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                }, 5000);
            }
        });
    });

    // Auto-hide alerts after 5 seconds
    document.querySelectorAll('.alert').forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.3s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });
});
