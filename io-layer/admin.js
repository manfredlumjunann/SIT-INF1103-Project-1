// Contract Clause Analyzer - administrator review page.
// Lists every user's submitted files (users expand and collapse) and lets the administrator
// write one comment per flagged clause. Customers see those comments but cannot change them.

const COMMENT_MAX_LENGTH = 2000;   // must match ClauseComment.MAX_LENGTH in data-layer/clause_comment.py

const usersView = document.getElementById('users-view');
const usersList = document.getElementById('users-list');
const usersMessage = document.getElementById('users-message');
const reviewView = document.getElementById('review-view');
const reviewContent = document.getElementById('review-content');

const expandedUserIds = new Set();   // kept so the lists reopen as they were after reviewing a file

document.getElementById('back-btn').addEventListener('click', closeReview);
document.getElementById('logout-btn').addEventListener('click', logout);
initThemeToggle('theme-toggle');

// ---------- Users and their files ----------

async function loadUsers() {
    try {
        const [users, analyses] = await Promise.all([
            fetchJson('/api/admin/users'),
            fetchJson('/api/admin/analyses')
        ]);
        renderUsers(users.users || [], analyses.analyses || []);
    } catch (error) {
        showUsersMessage(toDisplayError(error).message);
    }
}

function showUsersMessage(message) {
    usersMessage.textContent = message;
    usersMessage.hidden = !message;
}

function renderUsers(users, analyses) {
    const filesByOwner = new Map();
    analyses.forEach(entry => {
        if (!filesByOwner.has(entry.owner_id)) filesByOwner.set(entry.owner_id, []);
        filesByOwner.get(entry.owner_id).push(entry);
    });

    // Administrators only appear if they have submitted something themselves
    const visible = users.filter(user => user.role !== 'admin' || filesByOwner.has(user.id));
    usersList.replaceChildren();
    showUsersMessage(visible.length === 0 ? 'No users have registered yet.' : '');
    visible.forEach(user => usersList.appendChild(renderUserGroup(user, filesByOwner.get(user.id) || [])));
}

function renderUserGroup(user, files) {
    const group = createElement('div', 'user-group');
    const panelId = `files-${user.id}`;

    const header = createElement('button', 'user-group-header');
    header.type = 'button';
    header.setAttribute('aria-controls', panelId);
    header.appendChild(createElement('span', 'chevron', '▸'));
    header.appendChild(createElement('span', 'user-group-name', user.username));
    header.appendChild(createElement('span', 'user-group-count',
        `${files.length} file${files.length === 1 ? '' : 's'}`));
    group.appendChild(header);

    const panel = createElement('ul', 'file-list');
    panel.id = panelId;
    if (files.length === 0) {
        panel.appendChild(createElement('li', 'file-empty', 'No files submitted yet.'));
    }
    files.forEach(entry => panel.appendChild(renderFileRow(entry)));
    group.appendChild(panel);

    const setOpen = (open) => {
        header.setAttribute('aria-expanded', String(open));
        header.querySelector('.chevron').textContent = open ? '▾' : '▸';
        panel.hidden = !open;
        if (open) expandedUserIds.add(user.id);
        else expandedUserIds.delete(user.id);
    };
    header.addEventListener('click', () => setOpen(panel.hidden));
    setOpen(expandedUserIds.has(user.id));
    return group;
}

function renderFileRow(entry) {
    const item = createElement('li');
    const button = createElement('button', 'file-row');
    button.type = 'button';

    const name = createElement('span', 'file-row-main');
    name.appendChild(createElement('span', 'file-row-name', entry.filename || 'Contract'));
    name.appendChild(createElement('span', 'file-row-date', formatDateTime(entry.timestamp)));
    button.appendChild(name);

    const meta = createElement('span', 'file-row-meta');
    meta.appendChild(createElement('span', `status-tag ${entry.status}`, STATUS_LABELS[entry.status] || entry.status));
    RISK_LEVELS.forEach(level => {
        const count = (entry.counts && entry.counts[level]) || 0;
        if (count > 0) meta.appendChild(createElement('span', `pill ${level}`, `${count} ${level}`));
    });
    if (entry.total_flagged > 0) {
        meta.appendChild(createElement('span', 'comment-progress',
            `${entry.commented_count || 0}/${entry.total_flagged} commented`));
    }
    button.appendChild(meta);

    button.addEventListener('click', () => openReview(entry));
    item.appendChild(button);
    return item;
}

// ---------- Reviewing one file ----------

async function openReview(entry) {
    usersView.hidden = true;
    reviewView.hidden = false;
    reviewContent.replaceChildren(renderThinking('Loading the file...'));
    window.scrollTo(0, 0);
    try {
        const analysis = await fetchAnalysisJson(`/api/analyses/${encodeURIComponent(entry.id)}`);
        reviewContent.replaceChildren(renderReview(entry, analysis));
    } catch (error) {
        reviewContent.replaceChildren(renderErrorCard(toDisplayError(error), [['Back', closeReview]]));
    }
}

function closeReview() {
    reviewView.hidden = true;
    usersView.hidden = false;
    reviewContent.replaceChildren();
    loadUsers();   // refreshes the "n/m commented" counts
}

function renderReview(entry, analysis) {
    const wrapper = createElement('div', 'review');
    wrapper.appendChild(createElement('h1', 'admin-title', analysis.filename || 'Contract'));
    wrapper.appendChild(createElement('p', 'result-meta',
        [`Submitted by ${entry.owner_username}`, formatDateTime(analysis.timestamp)].join(' · ')));
    if (analysis.context) wrapper.appendChild(createElement('p', 'review-context', `Context: ${analysis.context}`));

    if (analysis.clauses.length === 0) {
        wrapper.appendChild(renderNotice(analysis.message || 'No problematic clauses were detected in this file.'));
        return wrapper;
    }
    wrapper.appendChild(createElement('p', 'admin-sub',
        `${analysis.clauses.length} flagged clause${analysis.clauses.length === 1 ? '' : 's'}. ` +
        'Your comments are shown to the user who submitted the file; they cannot edit them.'));

    const list = createElement('div', 'clause-list');
    analysis.clauses.forEach((clause, index) => {
        list.appendChild(renderClause(clause, index, () => renderCommentEditor(analysis.id, clause)));
    });
    wrapper.appendChild(list);
    return wrapper;
}

// ---------- Comment editor (one per clause) ----------

function renderCommentEditor(analysisId, clause) {
    const editor = createElement('div', 'comment-editor');
    const textareaId = `comment-${analysisId}-${clause.position}`;

    const label = createElement('label', '', 'Your comment');
    label.htmlFor = textareaId;
    editor.appendChild(label);

    const textarea = createElement('textarea');
    textarea.id = textareaId;
    textarea.rows = 3;
    textarea.maxLength = COMMENT_MAX_LENGTH;
    textarea.placeholder = 'Write a comment for the user about this clause...';
    textarea.value = clause.comment ? clause.comment.text : '';
    editor.appendChild(textarea);

    const bar = createElement('div', 'comment-editor-bar');
    const status = createElement('span', 'comment-status');
    status.setAttribute('role', 'status');
    const saveBtn = createElement('button', 'btn-ghost', 'Save comment');
    saveBtn.type = 'button';
    const removeBtn = createElement('button', 'btn-ghost', 'Remove');
    removeBtn.type = 'button';
    bar.append(status, removeBtn, saveBtn);
    editor.appendChild(bar);

    const showSaved = () => {
        removeBtn.hidden = !clause.comment;
        status.className = 'comment-status';
        status.textContent = clause.comment ? `Saved ${formatDateTime(clause.comment.updated_at)}` : '';
    };
    const fail = (error) => {
        status.className = 'comment-status error';
        status.textContent = toDisplayError(error).message;
    };
    const send = async (makeRequest, onDone) => {
        saveBtn.disabled = removeBtn.disabled = true;
        status.className = 'comment-status';
        status.textContent = 'Saving...';
        try {
            onDone(await makeRequest());
            showSaved();
        } catch (error) {
            fail(error);
        } finally {
            saveBtn.disabled = removeBtn.disabled = false;
        }
    };

    const url = `/api/admin/analyses/${encodeURIComponent(analysisId)}/clauses/${clause.position}/comment`;
    saveBtn.addEventListener('click', () => {
        if (!textarea.value.trim()) {
            fail(new Error('Write a comment first, or use Remove to delete the existing one.'));
            return;
        }
        send(() => fetchJson(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: textarea.value })
        }), (data) => {
            clause.comment = data.comment;
            textarea.value = data.comment.text;
        });
    });
    removeBtn.addEventListener('click', () => {
        send(() => fetchJson(url, { method: 'DELETE' }), () => {
            clause.comment = null;
            textarea.value = '';
        });
    });

    showSaved();
    return editor;
}

// ---------- Sign-in state ----------

// Only administrators may use this page; customers go to the analyzer, visitors to the login page.
async function initPage() {
    try {
        const response = await fetch('/api/auth/me');
        if (response.status === 401) {
            goToLoginPage();
            return;
        }
        const data = await response.json();
        if (!data || !data.user || data.user.role !== 'admin') {
            window.location.replace('/index.html');
            return;
        }
        document.getElementById('user-name').textContent = data.user.username;
    } catch (error) {
        console.error('Checking sign-in failed:', error);
    }
    document.body.classList.remove('auth-pending');
    loadUsers();
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
        showUsersMessage('Could not sign out: the server could not be reached, so you are still signed in.');
    }
}

// The back button can restore this page from the browser's cache without re-running scripts
window.addEventListener('pageshow', (event) => {
    if (event.persisted) initPage();
});

initPage();
