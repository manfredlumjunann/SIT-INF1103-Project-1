// Contract Clause Analyzer - Login / Registration Page
const MIN_PASSWORD_LENGTH = 8;
// Must match USERNAME_PATTERN in data-layer/data_manager.py (checked case-insensitively)
const USERNAME_PATTERN = /^[a-z0-9._-]{3,30}$/i;

const MODE_TEXT = {
    signin: {
        title: 'Login',
        subtitle: 'Hi, welcome back 👋',
        submit: 'Login',
        busy: 'Logging in...',
        switchText: 'Not registered yet?',
        switchLink: 'Create an account ↗'
    },
    register: {
        title: 'Create account',
        subtitle: 'Pick a username and password to get started.',
        submit: 'Create account',
        busy: 'Creating account...',
        switchText: 'Already have an account?',
        switchLink: 'Login ↗'
    }
};

let mode = 'signin';   // 'signin' or 'register'

// DOM Elements
const authForm = document.getElementById('auth-form');
const submitBtn = document.getElementById('auth-submit');
const authError = document.getElementById('auth-error');
const passwordInput = document.getElementById('password');
const confirmInput = document.getElementById('confirm-password');
const passwordToggle = document.getElementById('password-toggle');

authForm.addEventListener('submit', handleSubmit);
document.getElementById('switch-mode').addEventListener('click', () => {
    setMode(mode === 'signin' ? 'register' : 'signin');
});
passwordToggle.addEventListener('click', togglePasswordVisibility);
initThemeToggle('theme-toggle');

function setMode(newMode) {
    mode = newMode;
    const text = MODE_TEXT[mode];
    const registering = mode === 'register';

    document.getElementById('auth-title').textContent = text.title;
    document.getElementById('auth-subtitle').textContent = text.subtitle;
    document.getElementById('switch-text').textContent = text.switchText;
    document.getElementById('switch-mode').textContent = text.switchLink;
    document.title = `${text.title} - Clause Analyzer`;
    submitBtn.textContent = text.submit;

    document.querySelectorAll('.register-only').forEach(el => {
        el.hidden = !registering;
    });
    passwordInput.autocomplete = registering ? 'new-password' : 'current-password';
    clearMessages();
    document.getElementById('username').focus();
}

// Shows or hides both password fields together
function togglePasswordVisibility() {
    const show = passwordToggle.getAttribute('aria-pressed') !== 'true';
    passwordInput.type = show ? 'text' : 'password';
    confirmInput.type = show ? 'text' : 'password';
    passwordToggle.setAttribute('aria-pressed', String(show));
    passwordToggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
}

function fieldValue(id) {
    return document.getElementById(id).value.trim();
}

// Returns {message, fieldId} for the first problem found, or null if the form is valid.
function validateForm() {
    const username = fieldValue('username');
    const password = passwordInput.value;

    if (!username) {
        return { message: 'Please enter your username.', fieldId: 'username' };
    }
    if (mode === 'register' && !USERNAME_PATTERN.test(username)) {
        return {
            message: "Username must be 3-30 characters using letters, numbers, '.', '_' or '-'.",
            fieldId: 'username'
        };
    }
    if (!password) {
        return { message: 'Please enter your password.', fieldId: 'password' };
    }
    if (mode === 'register') {
        if (password.length < MIN_PASSWORD_LENGTH) {
            return { message: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`, fieldId: 'password' };
        }
        if (password !== confirmInput.value) {
            return { message: 'Passwords do not match.', fieldId: 'confirm-password' };
        }
    }
    return null;
}

async function handleSubmit(event) {
    event.preventDefault();
    clearMessages();

    const problem = validateForm();
    if (problem) {
        showError(problem.message, problem.fieldId);
        return;
    }

    const registering = mode === 'register';
    const payload = { username: fieldValue('username'), password: passwordInput.value };

    submitBtn.disabled = true;
    submitBtn.textContent = MODE_TEXT[mode].busy;
    try {
        const response = await fetch(registering ? '/api/auth/register' : '/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (response.ok) {
            goToMainPage();
            return;
        }
        showError(await describeAuthError(response), registering && response.status === 409 ? 'username' : null);
        if (response.status === 401) passwordInput.value = '';
    } catch (error) {
        console.error('Auth request failed:', error);
        showError('Could not reach the server. Check that the application is running and try again.');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = MODE_TEXT[mode].submit;
    }
}

// Uses the backend's {"error": "..."} message when there is one (nginx error pages are HTML).
async function describeAuthError(response) {
    try {
        const data = await response.json();
        if (data && data.error) return String(data.error);
    } catch (error) {
        // fall through to the generic messages below
    }
    if (response.status >= 500) {
        return 'The server is not responding. Wait a moment and try again.';
    }
    return 'Something went wrong. Please try again.';
}

function showError(message, fieldId) {
    authError.textContent = message;
    authError.hidden = false;
    if (fieldId) {
        const field = document.getElementById(fieldId);
        field.setAttribute('aria-invalid', 'true');
        field.focus();
    }
}

function clearMessages() {
    authError.textContent = '';
    authError.hidden = true;
    authForm.querySelectorAll('[aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
}

function goToMainPage() {
    window.location.replace('/user_index.html');
}

// Skip the form entirely if this browser is already signed in.
async function redirectIfSignedIn() {
    try {
        const response = await fetch('/api/auth/me');
        if (response.ok) goToMainPage();
    } catch (error) {
        // Server unreachable: stay on the form; submitting will report the problem
    }
}

redirectIfSignedIn();
