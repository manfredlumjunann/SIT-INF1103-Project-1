// Contract Clause Analyzer - Frontend Application
let selectedFile = null;
let analysisResult = null;
// The most recent request, so "Try Again" can repeat whatever failed
let lastRequest = null;

// Must match what the backend can parse and nginx/Flask upload limits
const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt'];
const MAX_FILE_BYTES = 50 * 1024 * 1024;
// Slightly longer than nginx's 600s proxy timeout so the server's 504 arrives first
const REQUEST_TIMEOUT_MS = 620 * 1000;
const RISK_LEVELS = ['high', 'medium', 'low'];

// DOM Elements
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('file-info');
const analyzeBtn = document.getElementById('analyze-btn');
const loadingSection = document.getElementById('loading-section');
const outputSection = document.getElementById('output-section');
const errorSection = document.getElementById('error-section');
const progressFill = document.getElementById('progress-fill');
const loadingStatus = document.getElementById('loading-status');
const fileError = document.getElementById('file-error');
const resultNotice = document.getElementById('result-notice');
const resultMeta = document.getElementById('result-meta');
const historyFilter = document.getElementById('history-filter');
const historyList = document.getElementById('history-list');
const historyMessage = document.getElementById('history-message');

// Drag & Drop Handlers
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileSelect(files[0]);
    }
});

// Single handler for the whole drop zone, including the Browse button inside it.
// Clicks re-dispatched from the hidden input itself are ignored to avoid reopening the picker.
dropZone.addEventListener('click', (e) => {
    if (e.target === fileInput) return;
    fileInput.click();
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

function handleFileSelect(file) {
    clearFileError();
    const problem = validateFile(file);
    if (problem) {
        fileInput.value = '';
        showFileError(problem);
        return;
    }

    selectedFile = file;
    document.querySelector('.file-name').textContent = file.name;
    fileInfo.style.display = 'flex';
    dropZone.style.display = 'none';
    analyzeBtn.disabled = false;
}

// Returns a user-facing problem description, or null if the file is acceptable.
function validateFile(file) {
    const dotIndex = file.name.lastIndexOf('.');
    const fileExt = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : '';

    if (!ALLOWED_EXTENSIONS.includes(fileExt)) {
        return `"${file.name}" is not a supported file type. Please upload a PDF, DOCX or TXT file.`;
    }
    if (file.size === 0) {
        return `"${file.name}" is empty. Please choose a file that contains the contract text.`;
    }
    if (file.size > MAX_FILE_BYTES) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
        return `"${file.name}" is ${sizeMb} MB, which exceeds the 50 MB limit.`;
    }
    return null;
}

function showFileError(message) {
    fileError.textContent = message;
    fileError.style.display = 'block';
}

function clearFileError() {
    fileError.textContent = '';
    fileError.style.display = 'none';
}

function removeFile() {
    selectedFile = null;
    fileInput.value = '';
    fileInfo.style.display = 'none';
    dropZone.style.display = 'block';
    analyzeBtn.disabled = true;
    clearFileError();
}

// Analysis Handlers
analyzeBtn.addEventListener('click', runAnalysis);
historyFilter.addEventListener('change', loadHistory);

// Upload the selected file and analyze it.
function runAnalysis() {
    if (!selectedFile) return;
    const file = selectedFile;
    const contextText = document.getElementById('context-text').value.trim();
    performAnalysis(() => requestAnalysis(file, contextText));
}

// Shared loading/progress/error handling for any request that runs the AI pipeline.
async function performAnalysis(makeRequest) {
    lastRequest = makeRequest;

    // Show loading state
    loadingSection.style.display = 'block';
    outputSection.style.display = 'none';
    errorSection.style.display = 'none';
    analyzeBtn.disabled = true;
    progressFill.style.width = '0%';

    // Simulate progress updates
    let progress = 0;
    const progressInterval = setInterval(() => {
        progress += Math.random() * 15;
        if (progress > 90) progress = 90;
        progressFill.style.width = progress + '%';

        if (progress < 30) loadingStatus.textContent = 'Extracting text from document...';
        else if (progress < 60) loadingStatus.textContent = 'Analyzing clauses with AI...';
        else if (progress < 80) loadingStatus.textContent = 'Searching case law references...';
        else loadingStatus.textContent = 'Compiling final report...';
    }, 500);

    let result;
    try {
        result = await makeRequest();
        progressFill.style.width = '100%';
    } catch (error) {
        console.error('Analysis request failed:', error);
        showError(toDisplayError(error));
        return;
    } finally {
        clearInterval(progressInterval);
        loadingSection.style.display = 'none';
        analyzeBtn.disabled = !selectedFile;
        loadHistory();
    }

    showResult(result);
}

// Opens a saved analysis from history. No AI call, so no progress animation.
async function openSavedAnalysis(analysisId) {
    lastRequest = () => fetchAnalysisJson(`/api/analyses/${encodeURIComponent(analysisId)}`);
    errorSection.style.display = 'none';
    try {
        showResult(await lastRequest());
        outputSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
        console.error('Opening saved analysis failed:', error);
        outputSection.style.display = 'none';
        showError(toDisplayError(error));
    }
}

function showResult(result) {
    analysisResult = result;
    try {
        displayResults(result);
    } catch (error) {
        console.error('Rendering results failed:', error, result);
        outputSection.style.display = 'none';
        showError({
            title: 'Could Not Display Results',
            message: 'The analysis finished, but its results could not be displayed.',
            hint: 'Try again. If it keeps happening, report it to the team with the time it occurred.'
        });
    }
}

// Sends the contract to the backend and returns the parsed result.
function requestAnalysis(file, contextText) {
    const formData = new FormData();
    formData.append('contract', file);
    formData.append('context', contextText);
    return fetchAnalysisJson('/api/analyze', { method: 'POST', body: formData });
}

// fetchJson plus a check that the response is an analysis with a clause list.
async function fetchAnalysisJson(url, options) {
    const data = await fetchJson(url, options);
    if (!data || !Array.isArray(data.clauses)) {
        throw invalidResponseError();
    }
    return data;
}

// Fetches JSON from the backend.
// Throws an error carrying a user-facing title/message/hint on any failure.
async function fetchJson(url, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
        let response;
        try {
            response = await fetch(url, { ...options, signal: controller.signal });
        } catch (error) {
            if (error.name === 'AbortError') throw timeoutError();
            throw appError('Connection Problem',
                'Could not reach the analysis server.',
                'Check that the application is running and your internet connection is working, then try again.');
        }

        if (!response.ok) {
            const serverMessage = await readServerError(response);
            const details = describeHttpError(response.status, serverMessage);
            throw appError(details.title, details.message, details.hint);
        }

        try {
            return await response.json();
        } catch (error) {
            if (error.name === 'AbortError') throw timeoutError();
            throw invalidResponseError();
        }
    } finally {
        clearTimeout(timeoutId);
    }
}

// Extracts the backend's {"error": "..."} message; nginx error pages are HTML, so fall back to ''.
async function readServerError(response) {
    try {
        const data = JSON.parse(await response.text());
        return data && data.error ? String(data.error) : '';
    } catch (error) {
        return '';
    }
}

function describeHttpError(status, serverMessage) {
    switch (status) {
        case 400:
            return {
                title: 'Could Not Analyze This File',
                message: serverMessage || 'The file could not be processed.',
                hint: 'Check that the file is a readable PDF, DOCX or TXT. Scanned PDFs (images of pages) contain no text to analyze.'
            };
        case 404:
            return {
                title: 'Analysis Not Found',
                message: serverMessage || 'This saved analysis no longer exists.',
                hint: 'Refresh the page to reload your list of saved analyses.'
            };
        case 413:
            return {
                title: 'File Too Large',
                message: 'The contract exceeds the 50 MB upload limit.',
                hint: 'Try a smaller file, or save the contract as a TXT or DOCX file.'
            };
        case 502:
            return {
                title: 'Analysis Service Unavailable',
                message: 'The analysis server is not responding.',
                hint: 'It may still be starting up. Wait a moment and try again.'
            };
        case 503:
            return {
                title: 'AI Service Unavailable',
                message: serverMessage || 'The AI models are currently unavailable.',
                hint: 'This is usually temporary. Try again in a few minutes.'
            };
        case 504:
            return timeoutDetails();
        default:
            return {
                title: 'Server Error',
                message: serverMessage || `The server returned an unexpected error (HTTP ${status}).`,
                hint: 'Try again. If it keeps happening, report it to the team with the time it occurred.'
            };
    }
}

function timeoutDetails() {
    return {
        title: 'Analysis Timed Out',
        message: 'The analysis took longer than 10 minutes and was stopped.',
        hint: 'Try again, or upload a shorter contract.'
    };
}

function timeoutError() {
    const details = timeoutDetails();
    return appError(details.title, details.message, details.hint);
}

function invalidResponseError() {
    return appError('Unexpected Response',
        'The server returned a response that could not be read.',
        'Try again. If it keeps happening, report it to the team with the time it occurred.');
}

function appError(title, message, hint) {
    const error = new Error(message);
    error.title = title;
    error.hint = hint;
    return error;
}

function toDisplayError(error) {
    return {
        title: error.title || 'Analysis Failed',
        message: error.message || 'Something went wrong while analyzing the contract.',
        hint: error.hint || 'Try again. If it keeps happening, report it to the team with the time it occurred.'
    };
}

function displayResults(result) {
    const clausesContainer = document.getElementById('clauses-container');
    clausesContainer.innerHTML = '';
    hideNotice();
    renderResultMeta(result);

    // Update stats
    const counts = { high: 0, medium: 0, low: 0, unrated: 0 };
    result.clauses.forEach(clause => {
        if (clause && typeof clause === 'object') counts[normaliseRisk(clause)]++;
    });

    document.getElementById('high-count').textContent = counts.high;
    document.getElementById('medium-count').textContent = counts.medium;
    document.getElementById('low-count').textContent = counts.low;

    if (result.clauses.length === 0) {
        showNotice(result.message || 'No problematic clauses were detected in this contract.',
            'info',
            'If you expected results, try again: the AI occasionally returns a response that cannot be read.');
        outputSection.style.display = 'block';
        return;
    }

    // Render each clause; a malformed clause is skipped instead of breaking the whole report
    let skipped = 0;
    result.clauses.forEach((clause, index) => {
        try {
            clausesContainer.appendChild(renderClause(clause, index));
        } catch (error) {
            skipped++;
            console.error(`Could not render clause ${index + 1}:`, error, clause);
        }
    });

    const warnings = [];
    if (counts.unrated > 0) {
        warnings.push(`${counts.unrated} clause(s) had no valid risk level and are shown as "Unrated".`);
    }
    if (skipped > 0) {
        warnings.push(`${skipped} clause(s) could not be displayed because the AI returned incomplete data.`);
    }
    if (warnings.length > 0) {
        showNotice(warnings.join(' '), 'warning');
    }

    outputSection.style.display = 'block';
}

// "nda.pdf · 29 Sep 2026, 16:45 · Context: ..." plus a tag when the result came from the cache.
function renderResultMeta(result) {
    resultMeta.textContent = '';
    if (!result.filename && !result.timestamp) {
        resultMeta.style.display = 'none';
        return;
    }
    const parts = [result.filename || 'Contract', formatDate(result.timestamp)];
    parts.push(result.context ? `Context: ${result.context}` : 'No context given');
    resultMeta.appendChild(document.createTextNode(parts.join(' · ')));
    if (result.cached) {
        resultMeta.appendChild(createElement('span', 'saved-tag',
            '♻️ Saved result: same contract and context, no new AI call'));
    }
    resultMeta.style.display = 'block';
}

function formatDate(timestamp) {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime())
        ? 'Unknown date'
        : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// ---------- History ----------

const STATUS_LABELS = { done: 'Done', failed: 'Failed', processing: 'Processing' };

async function loadHistory() {
    const query = historyFilter.value ? `?${historyFilter.value}` : '';
    let entries;
    try {
        const data = await fetchJson(`/api/analyses${query}`);
        entries = Array.isArray(data && data.analyses) ? data.analyses : [];
    } catch (error) {
        console.error('Loading history failed:', error);
        historyList.textContent = '';
        showHistoryMessage('Could not load your saved analyses. Refresh the page to try again.');
        return;
    }

    historyList.textContent = '';
    if (entries.length === 0) {
        showHistoryMessage(historyFilter.value
            ? 'No saved analyses match this filter.'
            : 'No saved analyses yet. Analyze a contract and it will appear here.');
        return;
    }
    historyMessage.style.display = 'none';
    entries.forEach(entry => {
        try {
            historyList.appendChild(renderHistoryItem(entry));
        } catch (error) {
            console.error('Could not render history entry:', error, entry);
        }
    });
}

function showHistoryMessage(message) {
    historyMessage.textContent = message;
    historyMessage.style.display = 'block';
}

function renderHistoryItem(entry) {
    const status = STATUS_LABELS[entry.status] ? entry.status : 'processing';
    const counts = entry.counts || {};

    const item = document.createElement('li');
    const button = createElement('button', 'history-item');
    button.type = 'button';
    button.addEventListener('click', () => openSavedAnalysis(entry.id));

    const top = createElement('div', 'history-row');
    top.appendChild(createElement('span', 'history-file', `📄 ${entry.filename || 'Contract'}`));
    top.appendChild(createElement('span', 'history-date', formatDate(entry.timestamp)));
    top.appendChild(createElement('span', `history-status ${status}`, STATUS_LABELS[status]));
    button.appendChild(top);

    const bottom = createElement('div', 'history-row');
    bottom.appendChild(createElement('span', 'history-context',
        entry.context ? `"${entry.context}"` : 'No context given'));
    if (status === 'done') {
        const risks = createElement('span', 'history-counts');
        risks.appendChild(createElement('span', 'count high', `🔴 ${counts.high || 0}`));
        risks.appendChild(createElement('span', 'count medium', `🟡 ${counts.medium || 0}`));
        risks.appendChild(createElement('span', 'count low', `🟢 ${counts.low || 0}`));
        bottom.appendChild(risks);
    }
    button.appendChild(bottom);

    item.appendChild(button);
    return item;
}

// Maps an AI-supplied risk level to high/medium/low, or 'unrated' if missing or unrecognised.
function normaliseRisk(clause) {
    const level = String((clause && clause.risk_level) || '').trim().toLowerCase();
    return RISK_LEVELS.includes(level) ? level : 'unrated';
}

function renderClause(clause, index) {
    if (!clause || typeof clause !== 'object') {
        throw new Error('Clause is not an object');
    }
    const risk = normaliseRisk(clause);
    const riskLabel = risk === 'unrated' ? 'Unrated' : `${risk.toUpperCase()} Risk`;

    // Built with textContent only: AI and web-search text is untrusted and must never be parsed as HTML.
    const card = createElement('div', `clause-card ${risk}`);

    const header = createElement('div', 'clause-header');
    header.appendChild(createElement('span', 'clause-type', `${index + 1}. ${clause.clause_type || 'Unnamed clause'}`));
    header.appendChild(createElement('span', `risk-badge ${risk}`, riskLabel));
    card.appendChild(header);

    card.appendChild(createElement('div', 'clause-text', `"${clause.clause_text || 'Clause text not provided'}"`));

    const workaround = createElement('div', 'workaround');
    workaround.appendChild(createElement('h4', '', '💡 Recommended Workaround'));
    workaround.appendChild(createElement('p', '',
        clause.workaround || 'No workaround was provided. Consider consulting legal counsel.'));
    card.appendChild(workaround);

    const references = Array.isArray(clause.legal_references)
        ? clause.legal_references.filter(ref => ref && typeof ref === 'object')
        : [];
    if (references.length > 0) {
        const section = createElement('div', 'references');
        section.appendChild(createElement('h4', '', '📚 Legal References & Precedents'));
        references.forEach(ref => section.appendChild(renderReference(ref)));
        card.appendChild(section);
    }
    return card;
}

function renderReference(ref) {
    const item = createElement('div', 'reference-item');
    const title = String(ref.title || ref.url || 'Untitled source');
    const href = safeUrl(ref.url);

    if (href) {
        const link = createElement('a', '', title);
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.appendChild(link);
    } else {
        item.appendChild(createElement('span', 'reference-title', title));
    }

    const summary = String(ref.summary || '');
    if (summary) {
        const shortened = summary.length > 200 ? `${summary.substring(0, 200)}...` : summary;
        item.appendChild(createElement('div', 'reference-summary', shortened));
    }
    return item;
}

// Only http(s) links are allowed; anything else (e.g. javascript: URLs) is shown as plain text.
function safeUrl(url) {
    try {
        const parsed = new URL(String(url || ''));
        return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null;
    } catch (error) {
        return null;
    }
}

function createElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
}

function showNotice(message, type, hint) {
    resultNotice.className = `result-notice ${type}`;
    resultNotice.textContent = message;
    if (hint) {
        const hintEl = document.createElement('span');
        hintEl.className = 'notice-hint';
        hintEl.textContent = hint;
        resultNotice.appendChild(hintEl);
    }
    resultNotice.style.display = 'block';
}

function hideNotice() {
    resultNotice.textContent = '';
    resultNotice.style.display = 'none';
}

function showError(details) {
    document.getElementById('error-title').textContent = details.title;
    document.getElementById('error-message').textContent = details.message;
    document.getElementById('error-hint').textContent = details.hint || '';
    errorSection.style.display = 'block';
}

// Repeats whatever request last failed: an upload, a re-run, or opening a saved analysis.
function retryAnalysis() {
    errorSection.style.display = 'none';
    if (!lastRequest) {
        resetForm();
        return;
    }
    performAnalysis(lastRequest);
}

function resetForm() {
    selectedFile = null;
    analysisResult = null;
    fileInput.value = '';
    document.getElementById('context-text').value = '';
    fileInfo.style.display = 'none';
    dropZone.style.display = 'block';
    outputSection.style.display = 'none';
    errorSection.style.display = 'none';
    analyzeBtn.disabled = true;
    progressFill.style.width = '0%';
    clearFileError();
    hideNotice();
    resultMeta.style.display = 'none';
    lastRequest = null;
}

function exportReport(format) {
    if (!analysisResult) return;
    
    let content, filename, mimeType;
    
    if (format === 'json') {
        content = JSON.stringify(analysisResult, null, 2);
        filename = 'contract-analysis.json';
        mimeType = 'application/json';
    } else {
        content = generateMarkdown(analysisResult);
        filename = 'contract-analysis.md';
        mimeType = 'text/markdown';
    }
    
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function generateMarkdown(result) {
    let md = `# Contract Clause Analysis Report\n\n`;
    md += `**Generated:** ${new Date().toISOString()}\n\n`;
    md += `**Total Flagged Clauses:** ${result.clauses.length}\n\n---\n\n`;
    
    result.clauses.forEach((clause, i) => {
        md += `## ${i + 1}. ${clause.clause_type} [${clause.risk_level} RISK]\n\n`;
        md += `**Issue:** ${clause.issue_description}\n\n`;
        md += `### Clause Text\n> ${clause.clause_text}\n\n`;
        md += `### 💡 Recommended Workaround\n${clause.workaround}\n\n`;
        
        if (clause.legal_references && clause.legal_references.length > 0) {
            md += `### 📚 Legal References\n`;
            clause.legal_references.forEach(ref => {
                md += `- **[${ref.title}](${ref.url})**\n`;
                if (ref.summary) md += `  ${ref.summary}\n`;
            });
            md += '\n';
        }
        md += '---\n\n';
    });
    
    return md;
}

// Show saved analyses as soon as the page opens
loadHistory();
