// Contract Clause Analyzer - main page (chat-style UI)
// All text from the server (AI output, web-search results, filenames) is untrusted and is only
// ever inserted with textContent via createElement(), never parsed as HTML.

// ---------- Settings ----------

// Must match what the backend can parse and nginx/Flask upload limits
const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt'];
const MAX_FILE_BYTES = 50 * 1024 * 1024;
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
    item.className = 'history-entry';
    item.appendChild(button);

    // An analysis that is still running cannot be deleted (the server refuses), so no ✕ for it
    if (status !== 'processing') {
        const deleteBtn = createElement('button', 'icon-btn history-delete');
        deleteBtn.type = 'button';
        deleteBtn.title = 'Delete';
        deleteBtn.setAttribute('aria-label', `Delete ${entry.filename || 'analysis'}`);
        deleteBtn.appendChild(closeIcon());
        deleteBtn.addEventListener('click', () => openDeleteDialog(entry));
        item.appendChild(deleteBtn);
    }
    return item;
}

// ---------- Deleting a saved analysis ----------

const deleteDialog = document.getElementById('delete-dialog');
const deleteConfirmBtn = document.getElementById('delete-confirm');
const deleteCancelBtn = document.getElementById('delete-cancel');
const deleteError = document.getElementById('delete-error');
let pendingDelete = null;   // the history entry the dialog is asking about

deleteConfirmBtn.addEventListener('click', confirmDelete);
deleteCancelBtn.addEventListener('click', () => deleteDialog.close());
// Clicking the dimmed backdrop (outside the box) cancels, like Esc does
deleteDialog.addEventListener('click', (event) => {
    if (event.target === deleteDialog) deleteDialog.close();
});
deleteDialog.addEventListener('close', () => {
    pendingDelete = null;
});

function openDeleteDialog(entry) {
    pendingDelete = entry;
    document.getElementById('delete-filename').textContent = entry.filename || 'Contract';
    deleteError.hidden = true;
    setDeleteBusy(false);
    deleteDialog.showModal();
    deleteCancelBtn.focus();   // the safe choice is focused by default
}

async function confirmDelete() {
    if (!pendingDelete) return;
    const entry = pendingDelete;
    setDeleteBusy(true);
    deleteError.hidden = true;
    try {
        await fetchJson(`/api/analyses/${encodeURIComponent(entry.id)}`, { method: 'DELETE' });
    } catch (error) {
        console.error('Deleting analysis failed:', error);
        deleteError.textContent = toDisplayError(error).message;
        deleteError.hidden = false;
        setDeleteBusy(false);
        return;
    }
    // Leave the deleted analysis if it is the one on screen
    if (analysisResult && analysisResult.id === entry.id) startNewAnalysis();
    deleteDialog.close();
    loadHistory();
}

function setDeleteBusy(busy) {
    deleteConfirmBtn.disabled = busy;
    deleteCancelBtn.disabled = busy;
    deleteConfirmBtn.textContent = busy ? 'Deleting...' : 'Delete';
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