// Contract Clause Analyzer - helpers shared by the user and admin pages. Load before the page script.
// All text from the server (AI output, web-search results, filenames) is untrusted and is only
// ever inserted with textContent via createElement(), never parsed as HTML.

// ---------- Settings ----------

// Slightly longer than nginx's 600s proxy timeout so the server's 504 arrives first
const REQUEST_TIMEOUT_MS = 620 * 1000;
const RISK_LEVELS = ['high', 'medium', 'low'];
const STATUS_LABELS = { done: 'Done', failed: 'Failed', processing: 'In progress' };

// ---------- Requests ----------

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
        case 403:
            return {
                title: 'Not allowed',
                message: serverMessage || 'Your account is not allowed to view this.',
                hint: 'Sign in with an administrator account to see other users\' analyses.'
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

function goToLoginPage() {
    window.location.replace('/login.html');
}

// ---------- Result rendering ----------

function renderAssistantShell() {
    const message = createElement('div', 'message-assistant');
    const label = createElement('div', 'assistant-label');
    label.appendChild(brandMark());
    label.appendChild(createElement('span', '', 'Clause Analyzer'));
    message.appendChild(label);
    return message;
}

// A spinner with one line of text, as an assistant message.
function renderThinking(text) {
    const message = renderAssistantShell();
    const thinking = createElement('div', 'thinking');
    thinking.setAttribute('role', 'status');
    thinking.appendChild(createElement('span', 'spinner'));
    thinking.appendChild(createElement('span', 'thinking-text', text));
    message.appendChild(thinking);
    return message;
}

// An error as an assistant message. actions is a list of [button label, click handler].
function renderErrorCard(details, actions) {
    const message = renderAssistantShell();
    const card = createElement('div', 'error-card');
    card.setAttribute('role', 'alert');
    card.appendChild(createElement('h3', '', details.title));
    card.appendChild(createElement('p', '', details.message));
    if (details.hint) card.appendChild(createElement('p', 'error-hint', details.hint));

    const buttons = createElement('div', 'result-actions');
    actions.forEach(([label, onClick]) => buttons.appendChild(actionButton(label, onClick)));
    card.appendChild(buttons);
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

function closeIcon() {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', 'M6 6l12 12M18 6L6 18');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linecap', 'round');
    svg.appendChild(path);
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

// ---------- Comparison rendering ----------

const RISK_CHANGE_LABELS = { higher: 'Higher risk in B', lower: 'Lower risk in B', same: 'Same risk level' };

// Shows a /api/compare response. showOwners adds whose analysis each side is (admin page).
function renderComparison(comparison, showOwners = false) {
    const listOf = (value) => (Array.isArray(value) ? value : []);
    const inBoth = listOf(comparison.in_both);
    const onlyA = listOf(comparison.only_a);
    const onlyB = listOf(comparison.only_b);
    const changed = inBoth.filter(pair => pair.risk_change !== 'same').length;

    const message = renderAssistantShell();
    const sides = createElement('div', 'compare-sides');
    sides.appendChild(renderCompareSide('A', comparison.a, showOwners));
    sides.appendChild(renderCompareSide('B', comparison.b, showOwners));
    message.appendChild(sides);

    if (inBoth.length + onlyA.length + onlyB.length === 0) {
        message.appendChild(renderNotice('Neither analysis flagged any clauses, so there is nothing to compare.'));
        return message;
    }

    const summary = createElement('div', 'summary');
    summary.appendChild(createElement('span', 'summary-text',
        `${inBoth.length} in both (${changed} with a different risk level), `
        + `${onlyA.length} only in A, ${onlyB.length} only in B.`));
    message.appendChild(summary);
    if (comparison.same_contract) {
        message.appendChild(renderNotice('Both analyses are of the same contract text.', '',
            'Differences come from the context that was given, or from the AI flagging different clauses.'));
    }

    message.appendChild(renderCompareSection(`In both (${inBoth.length})`, inBoth.map(renderComparePair)));
    message.appendChild(renderCompareSection(`Only in A (${onlyA.length})`,
        onlyA.map((clause, index) => renderClause(clause, index))));
    message.appendChild(renderCompareSection(`Only in B (${onlyB.length})`,
        onlyB.map((clause, index) => renderClause(clause, index))));
    return message;
}

// One of the two analyses being compared: which file, when, and its risk counts.
function renderCompareSide(letter, summary, showOwner) {
    const side = createElement('div', 'compare-side');
    const title = createElement('div', 'compare-side-title');
    title.appendChild(createElement('span', 'compare-letter', letter));
    title.appendChild(createElement('span', 'compare-file', summary.filename || 'Contract'));
    side.appendChild(title);

    const details = [formatDateTime(summary.timestamp)];
    if (showOwner) details.unshift(summary.owner_username || 'Unknown user');
    side.appendChild(createElement('p', 'result-meta', details.join(' · ')));
    if (summary.context) side.appendChild(createElement('p', 'compare-context', `Context: ${summary.context}`));

    const counts = summary.counts || {};
    const pills = createElement('div', 'compare-counts');
    RISK_LEVELS.forEach(level => {
        pills.appendChild(createElement('span', `pill ${level}`, `${counts[level] || 0} ${level}`));
    });
    side.appendChild(pills);
    return side;
}

function renderCompareSection(heading, items) {
    const section = createElement('section', 'compare-section');
    section.appendChild(createElement('h3', '', heading));
    if (items.length === 0) {
        section.appendChild(createElement('p', 'compare-empty', 'None'));
        return section;
    }
    const list = createElement('div', 'clause-list');
    items.forEach(item => list.appendChild(item));
    section.appendChild(list);
    return section;
}

// A clause found in both analyses: the risk level in each, with both full versions on demand.
function renderComparePair(pair, index) {
    const riskA = normaliseRisk(pair.a);
    const riskB = normaliseRisk(pair.b);
    const change = RISK_CHANGE_LABELS[pair.risk_change] ? pair.risk_change : 'same';
    const typeA = String(pair.a.clause_type || 'Unnamed clause');
    const typeB = String(pair.b.clause_type || 'Unnamed clause');

    const card = createElement('article', `clause-card compare-pair ${change}`);
    const header = createElement('div', 'clause-header');
    const titleBlock = createElement('div');
    titleBlock.appendChild(createElement('span', 'clause-type', `${index + 1}. ${typeA}`));
    if (typeA.trim().toLowerCase() !== typeB.trim().toLowerCase()) {
        titleBlock.appendChild(createElement('span', 'clause-location', `Called "${typeB}" in B`));
    }
    header.appendChild(titleBlock);

    const shift = createElement('span', 'risk-shift');
    shift.appendChild(createElement('span', `pill ${riskA}`, riskA));
    shift.appendChild(createElement('span', 'risk-arrow', '→'));
    shift.appendChild(createElement('span', `pill ${riskB}`, riskB));
    header.appendChild(shift);
    card.appendChild(header);

    const note = createElement('p', 'compare-note');
    note.appendChild(createElement('span', `compare-change ${change}`, RISK_CHANGE_LABELS[change]));
    note.appendChild(createElement('span', '',
        pair.matched_on === 'text' ? 'Same clause text in both' : 'Matched by clause type'));
    card.appendChild(note);

    const details = createElement('details', 'compare-details');
    details.appendChild(createElement('summary', '', 'Show both versions'));
    const columns = createElement('div', 'compare-columns');
    [['A', pair.a], ['B', pair.b]].forEach(([letter, clause]) => {
        const column = createElement('div', 'compare-column');
        column.appendChild(createElement('span', 'compare-letter', letter));
        column.appendChild(renderClause(clause, index));
        columns.appendChild(column);
    });
    details.appendChild(columns);
    card.appendChild(details);
    return card;
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
