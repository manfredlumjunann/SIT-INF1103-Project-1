// Contract Clause Analyzer - admin page: every user's saved analyses, read-only, plus comparison.
// The server enforces admin access (403 for anyone else); this page only decides what to show.
// Rendering helpers and fetchJson come from common.js.

// ---------- State ----------

let selectedUser = null;        // the user whose analyses are listed; null means all users
let listedAnalyses = [];        // what the list currently shows
let comparePicks = [];          // up to two analyses, in the order picked: A, then B. Kept across users
let lastAction = null;          // repeats the last failed request when "Try again" is pressed
let activeRequestId = 0;        // responses from older requests are ignored

// ---------- DOM ----------

const app = document.getElementById('app');
const userList = document.getElementById('user-list');
const userMessage = document.getElementById('user-message');
const listView = document.getElementById('list-view');
const detailView = document.getElementById('detail-view');
const listTitle = document.getElementById('list-title');
const listSubtitle = document.getElementById('list-subtitle');
const listMessage = document.getElementById('list-message');
const analysisList = document.getElementById('analysis-list');
const thread = document.getElementById('thread');
const pageError = document.getElementById('page-error');
const compareTray = document.getElementById('compare-tray');
const compareSlots = document.getElementById('compare-slots');
const compareRunBtn = document.getElementById('compare-run');

// ---------- Event wiring ----------

document.getElementById('back-btn').addEventListener('click', showListView);
compareRunBtn.addEventListener('click', () => {
    if (comparePicks.length === 2) openComparison(comparePicks[0], comparePicks[1]);
});
document.getElementById('compare-clear').addEventListener('click', () => {
    comparePicks = [];
    updateCompareUi();
});
document.getElementById('sidebar-open').addEventListener('click', () => setSidebarOpen(true));
document.getElementById('sidebar-close').addEventListener('click', () => setSidebarOpen(false));
document.getElementById('sidebar-backdrop').addEventListener('click', () => setSidebarOpen(false));
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setSidebarOpen(false);
});
document.getElementById('logout-btn').addEventListener('click', logout);
initThemeToggle('theme-toggle');

// ---------- Sidebar: users ----------

async function loadUsers() {
    let users;
    try {
        const data = await fetchJson('/api/admin/users');
        users = Array.isArray(data && data.users) ? data.users : [];
    } catch (error) {
        console.error('Loading users failed:', error);
        userList.replaceChildren();
        userMessage.textContent = 'Could not load the users. Refresh the page to try again.';
        userMessage.hidden = false;
        return;
    }

    userMessage.hidden = true;
    const total = users.reduce((sum, user) => sum + (Number(user.analysis_count) || 0), 0);
    userList.replaceChildren(renderUserItem(null, 'All users', total));
    users.forEach(user => {
        try {
            userList.appendChild(renderUserItem(user, user.username, user.analysis_count));
        } catch (error) {
            console.error('Could not render user:', error, user);
        }
    });
    markSelectedUser();
}

// user is null for the "All users" entry.
function renderUserItem(user, label, analysisCount) {
    const count = Number(analysisCount) || 0;
    const item = document.createElement('li');
    const button = createElement('button', 'history-item');
    button.type = 'button';
    button.dataset.userId = user ? user.id : '';
    button.addEventListener('click', () => selectUser(user));

    const title = createElement('span', 'history-title', label);
    if (user && user.role === 'admin') title.appendChild(createElement('span', 'role-tag', 'Admin'));
    button.appendChild(title);
    button.appendChild(createElement('span', 'history-meta', `${count} ${count === 1 ? 'analysis' : 'analyses'}`));
    item.appendChild(button);
    return item;
}

function selectUser(user) {
    selectedUser = user;
    markSelectedUser();
    setSidebarOpen(false);
    showListView();
    loadAnalyses();
}

function markSelectedUser() {
    const selectedId = selectedUser ? selectedUser.id : '';
    userList.querySelectorAll('.history-item').forEach(button => {
        const active = button.dataset.userId === selectedId;
        button.classList.toggle('active', active);
        if (active) button.setAttribute('aria-current', 'true');
        else button.removeAttribute('aria-current');
    });
}

// ---------- Analyses of the selected user ----------

async function loadAnalyses() {
    const requestedFor = selectedUser;
    listTitle.textContent = selectedUser ? selectedUser.username : 'All users';
    listSubtitle.textContent = 'Loading analyses...';
    listMessage.hidden = true;
    analysisList.replaceChildren();

    let entries;
    try {
        const query = requestedFor ? `?user=${encodeURIComponent(requestedFor.id)}` : '';
        const data = await fetchJson(`/api/admin/analyses${query}`);
        entries = Array.isArray(data && data.analyses) ? data.analyses : [];
    } catch (error) {
        console.error('Loading analyses failed:', error);
        if (requestedFor !== selectedUser) return;
        listSubtitle.textContent = '';
        showListMessage(toDisplayError(error).message);
        return;
    }
    // Another user was selected while this request was running; its own request fills the list
    if (requestedFor !== selectedUser) return;

    listedAnalyses = entries;
    listSubtitle.textContent = `${entries.length} saved ${entries.length === 1 ? 'analysis' : 'analyses'}, newest first`;
    if (entries.length === 0) {
        showListMessage(selectedUser ? 'This user has not analysed any contracts yet.' : 'No contracts have been analysed yet.');
    }
    renderAnalysisRows();
}

function showListMessage(message) {
    listMessage.textContent = message;
    listMessage.hidden = false;
}

function renderAnalysisRows() {
    analysisList.replaceChildren();
    listedAnalyses.forEach(entry => {
        try {
            analysisList.appendChild(renderAnalysisRow(entry));
        } catch (error) {
            console.error('Could not render analysis:', error, entry);
        }
    });
}

function renderAnalysisRow(entry) {
    const status = STATUS_LABELS[entry.status] ? entry.status : 'processing';
    const counts = entry.counts || {};
    const slot = comparePicks.findIndex(pick => pick.id === entry.id);

    const row = createElement('li', slot >= 0 ? 'analysis-row picked' : 'analysis-row');
    const main = createElement('div', 'row-main');
    main.appendChild(createElement('span', 'row-title', entry.filename || 'Contract'));

    const meta = createElement('div', 'row-meta');
    if (!selectedUser) meta.appendChild(createElement('span', '', entry.owner_username || 'Unknown user'));
    meta.appendChild(createElement('span', '', formatDateTime(entry.timestamp)));
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
    main.appendChild(meta);
    if (entry.context) {
        const context = createElement('p', 'row-context', entry.context);
        context.title = entry.context;
        main.appendChild(context);
    }
    row.appendChild(main);

    const actions = createElement('div', 'row-actions');
    const openBtn = actionButton('Open', () => openAnalysis(entry));
    // Nothing to show until the analysis has finished or failed
    openBtn.disabled = status === 'processing';
    actions.appendChild(openBtn);

    const pickBtn = actionButton(slot >= 0 ? `Picked as ${slot === 0 ? 'A' : 'B'}` : 'Compare',
        () => toggleComparePick(entry));
    pickBtn.setAttribute('aria-pressed', String(slot >= 0));
    pickBtn.disabled = status !== 'done';   // only finished analyses can be compared
    actions.appendChild(pickBtn);
    row.appendChild(actions);
    return row;
}

// ---------- Opening one analysis ----------

async function openAnalysis(entry) {
    const requestId = ++activeRequestId;
    lastAction = () => openAnalysis(entry);
    showDetailView(renderThinking('Opening saved analysis...'));

    try {
        const result = await fetchAnalysisJson(`/api/analyses/${encodeURIComponent(entry.id)}`);
        if (requestId !== activeRequestId) return;
        const owner = createElement('p', 'admin-owner', `Analysed by ${entry.owner_username || 'Unknown user'}`);
        showDetailView(owner, renderResult(result));
    } catch (error) {
        console.error('Opening analysis failed:', error);
        if (requestId === activeRequestId) showDetailView(renderError(toDisplayError(error)));
    }
}

// ---------- Comparing two analyses (of the same user or of different users) ----------

function toggleComparePick(entry) {
    const position = comparePicks.findIndex(pick => pick.id === entry.id);
    if (position >= 0) {
        comparePicks.splice(position, 1);
    } else {
        if (comparePicks.length === 2) comparePicks.shift();   // a third pick replaces the first
        comparePicks.push(entry);
    }
    updateCompareUi();
}

// Brings the tray and the list's Compare buttons in line with the current picks.
function updateCompareUi() {
    compareTray.hidden = comparePicks.length === 0;
    compareRunBtn.disabled = comparePicks.length !== 2;
    compareSlots.replaceChildren(renderCompareSlot('A', comparePicks[0]), renderCompareSlot('B', comparePicks[1]));
    renderAnalysisRows();
}

function renderCompareSlot(letter, pick) {
    const slot = createElement('div', pick ? 'compare-slot' : 'compare-slot empty');
    slot.appendChild(createElement('span', 'compare-letter', letter));
    if (!pick) {
        slot.appendChild(createElement('span', 'compare-slot-text', 'Pick another analysis, from any user'));
        return slot;
    }
    slot.appendChild(createElement('span', 'compare-slot-text',
        `${pick.filename || 'Contract'} · ${pick.owner_username || 'Unknown user'}`));
    const removeBtn = createElement('button', 'icon-btn');
    removeBtn.type = 'button';
    removeBtn.title = 'Remove';
    removeBtn.setAttribute('aria-label', `Remove ${pick.filename || 'analysis'} from the comparison`);
    removeBtn.appendChild(closeIcon());
    removeBtn.addEventListener('click', () => toggleComparePick(pick));
    slot.appendChild(removeBtn);
    return slot;
}

// No AI call: the server works out the differences from the two saved analyses.
async function openComparison(pickA, pickB) {
    const requestId = ++activeRequestId;
    lastAction = () => openComparison(pickA, pickB);
    showDetailView(renderThinking('Comparing the two analyses...'));

    try {
        const comparison = await fetchJson(
            `/api/compare?a=${encodeURIComponent(pickA.id)}&b=${encodeURIComponent(pickB.id)}`);
        if (requestId === activeRequestId) showDetailView(renderComparison(comparison, true));
    } catch (error) {
        console.error('Comparing analyses failed:', error);
        if (requestId === activeRequestId) {
            showDetailView(renderError({ ...toDisplayError(error), title: 'Could not compare' }));
        }
    }
}

// ---------- Switching between the list and one analysis / comparison ----------

function showDetailView(...content) {
    thread.replaceChildren(...content);
    listView.hidden = true;
    detailView.hidden = false;
    document.getElementById('content').scrollTop = 0;
}

function showListView() {
    activeRequestId++;   // a still-running request will no longer replace the screen
    lastAction = null;
    thread.replaceChildren();
    detailView.hidden = true;
    listView.hidden = false;
}

function renderError(details) {
    return renderErrorCard(details, [
        ['Try again', () => { if (lastAction) lastAction(); }],
        ['Back to analyses', showListView]
    ]);
}

function setSidebarOpen(open) {
    app.classList.toggle('sidebar-open', open);
}

// ---------- Sign-in state ----------

// Only signed-in admins may use this page; normal users go to their own page.
async function initPage() {
    try {
        const response = await fetch('/api/auth/me');
        if (response.status === 401) {
            goToLoginPage();
            return;
        }
        const data = await response.json();
        const user = data && data.user;
        if (!user || user.role !== 'admin') {
            window.location.replace('/user_index.html');
            return;
        }
        document.getElementById('user-name').textContent = user.username;
        document.getElementById('user-avatar').textContent = user.username.charAt(0);
    } catch (error) {
        console.error('Checking sign-in failed:', error);
    }
    document.body.classList.remove('auth-pending');
    loadUsers();
    loadAnalyses();
}

async function logout() {
    const logoutBtn = document.getElementById('logout-btn');
    logoutBtn.disabled = true;
    pageError.hidden = true;
    try {
        const response = await fetch('/api/auth/logout', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        goToLoginPage();
    } catch (error) {
        console.error('Sign out failed:', error);
        logoutBtn.disabled = false;
        showListView();
        pageError.textContent = 'Could not sign out: the server could not be reached, so you are still signed in. Try again in a moment.';
        pageError.hidden = false;
    }
}

// The back button can restore this page from the browser's cache without re-running scripts,
// so re-check the session when that happens (e.g. after signing out).
window.addEventListener('pageshow', (event) => {
    if (event.persisted) initPage();
});

initPage();
