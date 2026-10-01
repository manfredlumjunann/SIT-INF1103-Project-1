// Contract Clause Analyzer - main page (chat-style UI)
// All text from the server (AI output, web-search results, filenames) is untrusted and is only
// ever inserted with textContent via createElement(), never parsed as HTML.

// ---------- Settings ----------

// Must match what the backend can parse and nginx/Flask upload limits
const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt'];
const MAX_FILE_BYTES = 50 * 1024 * 1024;
// Slightly longer than nginx's 600s proxy timeout so the server's 504 arrives first
const REQUEST_TIMEOUT_MS = 620 * 1000;
const RISK_LEVELS = ['high', 'medium', 'low'];
const STATUS_LABELS = { done: 'Done', failed: 'Failed', processing: 'In progress' };
const DEFAULT_HINT = 'Attach a contract (PDF, Word or TXT, up to 50 MB), then press ↑ to analyse.';
const READY_HINT = 'Ready. Add context if you like, then enter ↑ or Enter to analyse.';
// Shown one after another while waiting, so long analyses still feel alive
const PROGRESS_STEPS = [
    'Reading the contract...',
    'Checking each clause with AI...',
    'Looking up legal references...',
    'Writing suggested workarounds...',
    'Still working. Long contracts can take several minutes...'
];

// ---------- State ----------

let selectedFile = null;
let analysisResult = null;
let lastAction = null;          // repeats the last failed request when "Try again" is pressed
let activeRequestId = 0;        // responses from older requests are ignored

// ---------- DOM ----------

const app = document.getElementById('app');
const thread = document.getElementById('thread');
const composer = document.getElementById('composer');
const contextInput = document.getElementById('context-text');
const fileInput = document.getElementById('file-input');
const attachBtn = document.getElementById('attach-btn');
const fileInfo = document.getElementById('file-info');
const fileError = document.getElementById('file-error');
const composerHint = document.getElementById('composer-hint');
const analyzeBtn = document.getElementById('analyze-btn');
const historyFilter = document.getElementById('history-filter');
const historyList = document.getElementById('history-list');
const historyMessage = document.getElementById('history-message');

// ---------- Event wiring ----------

attachBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) handleFileSelect(fileInput.files[0]);
});
document.getElementById('remove-file').addEventListener('click', removeFile);
composer.addEventListener('submit', (event) => {
    event.preventDefault();
    runAnalysis();
});
contextInput.addEventListener('keydown', (event) => {
    // Enter sends (like chat apps); Shift+Enter adds a new line
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (!analyzeBtn.disabled) runAnalysis();
    }
});
contextInput.addEventListener('input', autoResizeContext);
document.getElementById('new-analysis-btn').addEventListener('click', startNewAnalysis);
historyFilter.addEventListener('change', loadHistory);
document.getElementById('sidebar-open').addEventListener('click', () => setSidebarOpen(true));
document.getElementById('sidebar-close').addEventListener('click', () => setSidebarOpen(false));
document.getElementById('sidebar-backdrop').addEventListener('click', () => setSidebarOpen(false));
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setSidebarOpen(false);
});
document.getElementById('logout-btn').addEventListener('click', logout);
initThemeToggle('theme-toggle');
setUpDragAndDrop();

// ---------- File selection ----------

function setUpDragAndDrop() {
    const dropArea = document.querySelector('.main');
    let dragDepth = 0;   // dragenter/leave fire for every child element; count to avoid flicker

    dropArea.addEventListener('dragenter', (event) => {
        event.preventDefault();
        dragDepth++;
        composer.classList.add('drag-over');
    });
    dropArea.addEventListener('dragover', (event) => event.preventDefault());
    dropArea.addEventListener('dragleave', () => {
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) composer.classList.remove('drag-over');
    });
    dropArea.addEventListener('drop', (event) => {
        event.preventDefault();
        dragDepth = 0;
        composer.classList.remove('drag-over');
        const files = event.dataTransfer.files;
        if (files.length > 0) handleFileSelect(files[0]);
    });
}

function handleFileSelect(file) {
    clearFileError();
    const problem = validateFile(file);
    if (problem) {
        fileInput.value = '';
        showFileError(problem);
        return;
    }

    selectedFile = file;
    document.getElementById('file-name').textContent = file.name;
    fileInfo.hidden = false;
    attachBtn.hidden = true;
    composerHint.textContent = READY_HINT;
    analyzeBtn.disabled = false;
    contextInput.focus();
}

// Returns a user-facing problem description, or null if the file is acceptable.
function validateFile(file) {
    const dotIndex = file.name.lastIndexOf('.');
    const fileExt = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : '';

    if (!ALLOWED_EXTENSIONS.includes(fileExt)) {
        return `"${file.name}" is not a supported file type. Please upload a PDF, Word (.docx) or TXT file.`;
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

function removeFile() {
    selectedFile = null;
    fileInput.value = '';
    fileInfo.hidden = true;
    attachBtn.hidden = false;
    composerHint.textContent = DEFAULT_HINT;
    analyzeBtn.disabled = true;
    clearFileError();
}

function showFileError(message) {
    fileError.textContent = message;
    fileError.hidden = false;
}

function clearFileError() {
    fileError.textContent = '';
    fileError.hidden = true;
}

function autoResizeContext() {
    contextInput.style.height = 'auto';
    contextInput.style.height = `${contextInput.scrollHeight}px`;
}

// ---------- Running and opening analyses ----------

// Upload the attached file and analyse it. The file stays attached so a follow-up
// question can be asked about the same contract.
function runAnalysis() {
    if (!selectedFile) {
        showFileError('Attach a contract first.');
        return;
    }
    const file = selectedFile;
    const contextText = contextInput.value.trim();
    contextInput.value = '';
    autoResizeContext();
    performAnalysis(() => requestAnalysis(file, contextText), { filename: file.name, context: contextText });
}

// Shared loading/error handling for any request that runs the AI pipeline.
async function performAnalysis(makeRequest, userTurn) {
    const requestId = ++activeRequestId;
    lastAction = () => performAnalysis(makeRequest, userTurn);
    clearFileError();
    setActiveHistoryItem(null);
    showThread(userTurn, renderLoading());
    setBusy(true);

    const stopProgress = startProgressMessages();
    let result;
    try {
        result = await makeRequest();
    } catch (error) {
        console.error('Analysis request failed:', error);
        if (requestId === activeRequestId) showThread(userTurn, renderError(toDisplayError(error)));
        return;
    } finally {
        stopProgress();
        if (requestId === activeRequestId) setBusy(false);
        loadHistory();   // a finished, failed or timed-out analysis may now be in the list
    }

    if (requestId === activeRequestId) showResult(result);
}

// Opens a saved analysis from the sidebar. No AI call, so it is quick.
// Any attached file is cleared: it belongs to a different contract than the one being opened.
async function openSavedAnalysis(entry) {
    const requestId = ++activeRequestId;
    lastAction = () => openSavedAnalysis(entry);
    removeFile();
    setBusy(false);
    setSidebarOpen(false);
    setActiveHistoryItem(entry.id);
    showThread({ filename: entry.filename, context: entry.context }, renderLoading('Opening saved analysis...'));

    try {
        const result = await fetchAnalysisJson(`/api/analyses/${encodeURIComponent(entry.id)}`);
        if (requestId === activeRequestId) showResult(result);
    } catch (error) {
        console.error('Opening saved analysis failed:', error);
        if (requestId === activeRequestId) {
            showThread({ filename: entry.filename, context: entry.context }, renderError(toDisplayError(error)));
        }
    }
}

function showResult(result) {
    analysisResult = result;
    const userTurn = { filename: result.filename, context: result.context };
    try {
        showThread(userTurn, renderResult(result));
        setActiveHistoryItem(result.id);
    } catch (error) {
        console.error('Rendering results failed:', error, result);
        showThread(userTurn, renderError({
            title: 'Could not display results',
            message: 'The analysis finished, but its results could not be displayed.',
            hint: 'Try again. If it keeps happening, report it to the team with the time it occurred.'
        }));
    }
}

function startNewAnalysis() {
    activeRequestId++;   // a still-running request will no longer replace the screen
    analysisResult = null;
    lastAction = null;
    thread.replaceChildren();
    app.classList.remove('has-thread');
    setBusy(false);
    removeFile();
    contextInput.value = '';
    autoResizeContext();
    setActiveHistoryItem(null);
    setSidebarOpen(false);
    contextInput.focus();
}

function retryLastAction() {
    if (lastAction) lastAction();
    else startNewAnalysis();
}

function setBusy(busy) {
    analyzeBtn.disabled = busy || !selectedFile;
    attachBtn.disabled = busy;
    analyzeBtn.setAttribute('aria-label', busy ? 'Analysing...' : 'Analyse contract');
}

// Cycles through PROGRESS_STEPS on the loading message; returns a function that stops it.
function startProgressMessages() {
    let step = 0;
    const intervalId = setInterval(() => {
        step = Math.min(step + 1, PROGRESS_STEPS.length - 1);
        const status = thread.querySelector('.thinking-text');
        if (status) status.textContent = PROGRESS_STEPS[step];
    }, 12000);
    return () => clearInterval(intervalId);
}

// ---------- Requests ----------

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
            throw appError('Connection problem',
                'Could not reach the analysis server.',
                'Check that the application is running and your internet connection is working, then try again.');
        }

        if (response.status === 401) {
            // Session expired or signed out in another tab
            goToLoginPage();
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
                title: 'Could not analyse this file',
                message: serverMessage || 'The file could not be processed.',
                hint: 'Check that the file is a readable PDF, Word or TXT file. Scanned PDFs (images of pages) contain no text to analyse.'
            };
        case 401:
            return {
                title: 'Signed out',
                message: 'Your session has ended.',
                hint: 'Taking you to the login page...'
            };
        case 404:
            return {
                title: 'Analysis not found',
                message: serverMessage || 'This saved analysis no longer exists.',
                hint: 'Refresh the page to reload your saved analyses.'
            };
        case 413:
            return {
                title: 'File too large',
                message: 'The contract exceeds the 50 MB upload limit.',
                hint: 'Try a smaller file, or save the contract as a TXT or Word file.'
            };
        case 502:
            return {
                title: 'Analysis service unavailable',
                message: 'The analysis server is not responding.',
                hint: 'It may still be starting up. Wait a moment and try again.'
            };
        case 503:
            return {
                title: 'AI service unavailable',
                message: serverMessage || 'The AI models are currently unavailable.',
                hint: 'This is usually temporary. Try again in a few minutes.'
            };
        case 504:
            return timeoutDetails();
        default:
            return {
                title: 'Something went wrong',
                message: serverMessage || `The server returned an unexpected error (HTTP ${status}).`,
                hint: 'Try again. If it keeps happening, report it to the team with the time it occurred.'
            };
    }
}

function timeoutDetails() {
    return {
        title: 'This is taking longer than expected',
        message: 'No result arrived within 10 minutes.',
        hint: 'The analysis may still finish in the background. Check Analyses in the sidebar in a few minutes before trying again.'
    };
}

function timeoutError() {
    const details = timeoutDetails();
    return appError(details.title, details.message, details.hint);
}

function invalidResponseError() {
    return appError('Unexpected response',
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
        title: error.title || 'Analysis failed',
        message: error.message || 'Something went wrong while analysing the contract.',
        hint: error.hint || 'Try again. If it keeps happening, report it to the team with the time it occurred.'
    };
}

// ---------- Thread rendering ----------

// Replaces the conversation with the user's message (if they typed one) and the assistant reply.
// The contract itself is not repeated here: it stays shown in the input box.
function showThread(userTurn, assistantContent) {
    app.classList.add('has-thread');
    const userMessage = renderUserMessage(userTurn);
    thread.replaceChildren(...(userMessage ? [userMessage] : []), assistantContent);
    document.getElementById('content').scrollTop = 0;
}

// The typed context/question as a chat bubble, or null when nothing was typed.
function renderUserMessage({ context }) {
    if (!context) return null;
    const wrapper = createElement('div', 'message-user');
    wrapper.appendChild(createElement('div', 'user-bubble', context));
    return wrapper;
}

function renderAssistantShell() {
    const message = createElement('div', 'message-assistant');
    const label = createElement('div', 'assistant-label');
    label.appendChild(brandMark());
    label.appendChild(createElement('span', '', 'Clause Analyzer'));
    message.appendChild(label);
    return message;
}

function renderLoading(text = PROGRESS_STEPS[0]) {
    const message = renderAssistantShell();
    const thinking = createElement('div', 'thinking');
    thinking.setAttribute('role', 'status');
    thinking.appendChild(createElement('span', 'spinner'));
    thinking.appendChild(createElement('span', 'thinking-text', text));
    message.appendChild(thinking);
    if (text === PROGRESS_STEPS[0]) {
        message.appendChild(createElement('p', 'thinking-note',
            'Analyses usually take a few minutes. You can open a saved analysis meanwhile; this one will appear in the sidebar when it finishes.'));
    }
    return message;
}

function renderError(details) {
    const message = renderAssistantShell();
    const card = createElement('div', 'error-card');
    card.setAttribute('role', 'alert');
    card.appendChild(createElement('h3', '', details.title));
    card.appendChild(createElement('p', '', details.message));
    if (details.hint) card.appendChild(createElement('p', 'error-hint', details.hint));

    const actions = createElement('div', 'result-actions');
    actions.appendChild(actionButton('Try again', retryLastAction));
    actions.appendChild(actionButton('Start over', startNewAnalysis));
    card.appendChild(actions);
    message.appendChild(card);
    return message;
}

function renderResult(result) {
    const message = renderAssistantShell();
    message.appendChild(renderResultMeta(result));

    const clauses = result.clauses;
    const counts = { high: 0, medium: 0, low: 0, unrated: 0 };
    clauses.forEach(clause => {
        if (clause && typeof clause === 'object') counts[normaliseRisk(clause)]++;
    });

    if (clauses.length === 0) {
        message.appendChild(renderNotice(result.message || 'No problematic clauses were detected in this contract.',
            '', 'If you expected results, try again: the AI occasionally returns a response that cannot be read.'));
        message.appendChild(renderResultActions(result));
        return message;
    }

    const summary = createElement('div', 'summary');
    summary.appendChild(createElement('span', 'summary-text',
        `Found ${clauses.length} clause${clauses.length === 1 ? '' : 's'} to review:`));
    RISK_LEVELS.forEach(level => {
        summary.appendChild(createElement('span', `pill ${level}`, `${counts[level]} ${level}`));
    });
    if (counts.unrated > 0) summary.appendChild(createElement('span', 'pill unrated', `${counts.unrated} unrated`));
    message.appendChild(summary);

    // Render each clause; a malformed clause is skipped instead of breaking the whole report
    const list = createElement('div', 'clause-list');
    let skipped = 0;
    clauses.forEach((clause, index) => {
        try {
            list.appendChild(renderClause(clause, index));
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
    if (warnings.length > 0) message.appendChild(renderNotice(warnings.join(' '), 'warning'));

    message.appendChild(list);
    message.appendChild(renderResultActions(result));
    return message;
}

// "nda.pdf · 29 Sep 2026, 4:45 PM" plus a tag when the result came from the cache.
function renderResultMeta(result) {
    const meta = createElement('p', 'result-meta',
        [result.filename || 'Contract', formatDateTime(result.timestamp)].join(' · '));
    if (result.cached) {
        meta.appendChild(createElement('span', 'saved-tag', 'Saved result, no new AI call'));
    }
    return meta;
}

function renderNotice(text, type, hint) {
    const notice = createElement('div', `notice ${type || ''}`.trim(), text);
    if (hint) notice.appendChild(createElement('span', 'notice-hint', hint));
    return notice;
}

function renderResultActions(result) {
    const actions = createElement('div', 'result-actions');
    if (result.clauses.length > 0) {
        actions.appendChild(actionButton('Export as Markdown', () => exportReport(result, 'markdown')));
    }
    actions.appendChild(actionButton('Export as JSON', () => exportReport(result, 'json')));
    return actions;
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
    const card = createElement('article', `clause-card ${risk}`);

    const header = createElement('div', 'clause-header');
    const titleBlock = createElement('div');
    titleBlock.appendChild(createElement('span', 'clause-type', `${index + 1}. ${clause.clause_type || 'Unnamed clause'}`));
    if (clause.line_number) titleBlock.appendChild(createElement('span', 'clause-location', clause.line_number));
    header.appendChild(titleBlock);
    header.appendChild(createElement('span', `pill ${risk}`, risk === 'unrated' ? 'Unrated' : `${risk} risk`));
    card.appendChild(header);

    if (clause.clause_text) card.appendChild(createElement('blockquote', 'clause-text', clause.clause_text));
    if (clause.issue_description) card.appendChild(renderClauseSection('Why it matters', clause.issue_description));
    card.appendChild(renderClauseSection('Suggested workaround',
        clause.workaround || 'No workaround was provided. Consider consulting legal counsel.'));

    const references = Array.isArray(clause.legal_references)
        ? clause.legal_references.filter(ref => ref && typeof ref === 'object')
        : [];
    if (references.length > 0) {
        const details = createElement('details', 'references');
        details.appendChild(createElement('summary', '',
            `${references.length} legal reference${references.length === 1 ? '' : 's'}`));
        references.forEach(ref => details.appendChild(renderReference(ref)));
        card.appendChild(details);
    }
    return card;
}

function renderClauseSection(heading, text) {
    const section = createElement('div', 'clause-section');
    section.appendChild(createElement('h4', '', heading));
    section.appendChild(createElement('p', '', text));
    return section;
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
    if (ref.domain) item.appendChild(createElement('span', 'reference-domain', ref.domain));

    const summary = String(ref.summary || '');
    if (summary) {
        const shortened = summary.length > 240 ? `${summary.substring(0, 240)}...` : summary;
        item.appendChild(createElement('p', 'reference-summary', shortened));
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

function actionButton(label, onClick) {
    const button = createElement('button', 'btn-ghost', label);
    button.type = 'button';
    button.addEventListener('click', onClick);
    return button;
}

// The logo, built with DOM methods so no HTML string is parsed
function brandMark() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'brand-mark');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('aria-hidden', 'true');
    const fill = document.createElementNS(ns, 'path');
    fill.setAttribute('d', 'M6 3h8l4 4v14H6z');
    fill.setAttribute('fill', 'currentColor');
    fill.setAttribute('opacity', '0.25');
    const outline = document.createElementNS(ns, 'path');
    outline.setAttribute('d', 'M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h4');
    outline.setAttribute('stroke', 'currentColor');
    outline.setAttribute('stroke-width', '1.8');
    outline.setAttribute('stroke-linejoin', 'round');
    outline.setAttribute('stroke-linecap', 'round');
    svg.append(fill, outline);
    return svg;
}

function formatDateTime(timestamp) {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime())
        ? 'Unknown date'
        : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function formatShortDate(timestamp) {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime())
        ? ''
        : date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

// ---------- Sidebar: saved analyses ----------

async function loadHistory() {
    const filterAtRequest = historyFilter.value;
    const query = filterAtRequest ? `?${filterAtRequest}` : '';
    let entries;
    try {
        const data = await fetchJson(`/api/analyses${query}`);
        entries = Array.isArray(data && data.analyses) ? data.analyses : [];
    } catch (error) {
        console.error('Loading history failed:', error);
        historyList.replaceChildren();
        showHistoryMessage('Could not load your saved analyses. Refresh the page to try again.');
        return;
    }
    // The filter changed while this request was running; a newer request will fill the list
    if (filterAtRequest !== historyFilter.value) return;

    historyList.replaceChildren();
    if (entries.length === 0) {
        showHistoryMessage(filterAtRequest
            ? 'No saved analyses match this filter.'
            : 'No analyses yet. Your analyses will appear here.');
        return;
    }
    historyMessage.hidden = true;
    entries.forEach(entry => {
        try {
            historyList.appendChild(renderHistoryItem(entry));
        } catch (error) {
            console.error('Could not render history entry:', error, entry);
        }
    });
    setActiveHistoryItem(analysisResult ? analysisResult.id : null);
}

function showHistoryMessage(message) {
    historyMessage.textContent = message;
    historyMessage.hidden = false;
}

function renderHistoryItem(entry) {
    const status = STATUS_LABELS[entry.status] ? entry.status : 'processing';
    const counts = entry.counts || {};

    const item = document.createElement('li');
    const button = createElement('button', 'history-item');
    button.type = 'button';
    button.dataset.id = entry.id;
    button.title = entry.context ? `${entry.filename}\n${entry.context}` : entry.filename || '';
    button.addEventListener('click', () => openSavedAnalysis(entry));

    button.appendChild(createElement('span', 'history-title', entry.filename || 'Contract'));
    const meta = createElement('span', 'history-meta');
    meta.appendChild(createElement('span', '', formatShortDate(entry.timestamp)));
    if (status === 'done') {
        const dots = createElement('span', 'risk-dots');
        RISK_LEVELS.forEach(level => {
            if (counts[level] > 0) {
                const dot = createElement('span', `risk-dot ${level}`, counts[level]);
                dot.title = `${counts[level]} ${level} risk`;
                dots.appendChild(dot);
            }
        });
        if (!dots.childElementCount) dots.appendChild(createElement('span', '', 'No issues found'));
        meta.appendChild(dots);
    } else {
        meta.appendChild(createElement('span', `status-tag ${status}`, STATUS_LABELS[status]));
    }
    button.appendChild(meta);

    item.appendChild(button);
    return item;
}

function setActiveHistoryItem(analysisId) {
    historyList.querySelectorAll('.history-item').forEach(button => {
        const active = analysisId !== null && button.dataset.id === analysisId;
        button.classList.toggle('active', active);
        if (active) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
    });
}

function setSidebarOpen(open) {
    app.classList.toggle('sidebar-open', open);
}

// ---------- Export ----------

function exportReport(result, format) {
    const baseName = exportBaseName(result.filename);
    const content = format === 'json' ? JSON.stringify(result, null, 2) : generateMarkdown(result);
    const filename = `${baseName}-analysis.${format === 'json' ? 'json' : 'md'}`;
    const mimeType = format === 'json' ? 'application/json' : 'text/markdown';

    const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

// "Supplier Agreement.pdf" -> "supplier-agreement"
function exportBaseName(filename) {
    const withoutExtension = String(filename || 'contract').replace(/\.[^.]+$/, '');
    const slug = withoutExtension.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return slug || 'contract';
}

function generateMarkdown(result) {
    const text = (value, fallback = 'Not provided') => String(value || fallback);
    let md = `# Contract Clause Analysis: ${text(result.filename, 'Contract')}\n\n`;
    md += `**Analysed:** ${formatDateTime(result.timestamp)}\n\n`;
    if (result.context) md += `**Context:** ${result.context}\n\n`;
    md += `**Flagged clauses:** ${result.clauses.length}\n\n---\n\n`;

    result.clauses.forEach((clause, i) => {
        if (!clause || typeof clause !== 'object') return;
        md += `## ${i + 1}. ${text(clause.clause_type, 'Unnamed clause')} [${normaliseRisk(clause).toUpperCase()}]\n\n`;
        if (clause.line_number) md += `*${clause.line_number}*\n\n`;
        md += `> ${text(clause.clause_text)}\n\n`;
        md += `**Why it matters:** ${text(clause.issue_description)}\n\n`;
        md += `**Suggested workaround:** ${text(clause.workaround)}\n\n`;

        const references = Array.isArray(clause.legal_references) ? clause.legal_references : [];
        if (references.length > 0) {
            md += `**Legal references:**\n`;
            references.forEach(ref => {
                if (!ref) return;
                const href = safeUrl(ref.url);
                md += href ? `- [${text(ref.title, href)}](${href})\n` : `- ${text(ref.title)}\n`;
                if (ref.summary) md += `  ${ref.summary}\n`;
            });
            md += '\n';
        }
        md += '---\n\n';
    });
    return md;
}

// ---------- Sign-in state ----------

// Only signed-in users may use the main page; everyone else goes to the login page.
async function initPage() {
    try {
        const response = await fetch('/api/auth/me');
        if (response.status === 401) {
            goToLoginPage();
            return;
        }
        const data = await response.json();
        showSignedInUser(data && data.user);
    } catch (error) {
        console.error('Checking sign-in failed:', error);
    }
    document.body.classList.remove('auth-pending');
    loadHistory();
}

function showSignedInUser(user) {
    const username = user && user.username ? user.username : '';
    document.getElementById('user-name').textContent = username;
    document.getElementById('user-avatar').textContent = username ? username.charAt(0) : '?';
    document.getElementById('greeting-text').textContent = username ? `What would you like to review?, ${username}` : 'Welcome back';
}

function goToLoginPage() {
    window.location.replace('/login.html');
}

async function logout() {
    const logoutBtn = document.getElementById('logout-btn');
    logoutBtn.disabled = true;
    try {
        const response = await fetch('/api/auth/logout', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        goToLoginPage();
    } catch (error) {
        console.error('Sign out failed:', error);
        logoutBtn.disabled = false;
        showFileError('Could not sign out: the server could not be reached, so you are still signed in. Try again in a moment.');
    }
}

// The back button can restore this page from the browser's cache without re-running scripts,
// so re-check the session when that happens (e.g. after signing out).
window.addEventListener('pageshow', (event) => {
    if (event.persisted) initPage();
});

initPage();
