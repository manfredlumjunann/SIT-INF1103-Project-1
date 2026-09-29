// Contract Clause Analyzer - Login / Registration Page
const MIN_PASSWORD_LENGTH = 8;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

let mode = 'signin';   // 'signin' or 'register'

// DOM Elements
const authForm = document.getElementById('auth-form');
const signinTab = document.getElementById('signin-tab');
const registerTab = document.getElementById('register-tab');
const submitBtn = document.getElementById('auth-submit');
const authError = document.getElementById('auth-error');
const authInfo = document.getElementById('auth-info');
const passwordInput = document.getElementById('password');

signinTab.addEventListener('click', () => setMode('signin'));
registerTab.addEventListener('click', () => setMode('register'));
authForm.addEventListener('submit', handleSubmit);

function setMode(newMode) {
    mode = newMode;
    const registering = mode === 'register';

    signinTab.classList.toggle('active', !registering);
    registerTab.classList.toggle('active', registering);
    signinTab.setAttribute('aria-selected', String(!registering));
    registerTab.setAttribute('aria-selected', String(registering));

    document.querySelectorAll('.register-only').forEach(el => {
        el.style.display = registering ? 'block' : 'none';
    });
    passwordInput.autocomplete = registering ? 'new-password' : 'current-password';
    submitBtn.textContent = registering ? 'Create Account' : 'Sign In';
    clearMessages();
}

function fieldValue(id) {
    return document.getElementById(id).value.trim();
}

// Returns {message, fieldId} for the first problem found, or null if the form is valid.
function validateForm() {
    const email = fieldValue('email');
    const password = passwordInput.value;

    if (mode === 'register' && !fieldValue('name')) {
        return { message: 'Please enter your full name.', fieldId: 'name' };
    }
    if (!email) {
        return { message: 'Please enter your email address.', fieldId: 'email' };
    }
    if (!EMAIL_PATTERN.test(email)) {
        return { message: 'Please enter a valid email address, e.g. name@company.com.', fieldId: 'email' };
    }
    if (!password) {
        return { message: 'Please enter your password.', fieldId: 'password' };
    }
    if (mode === 'register') {
        if (password.length < MIN_PASSWORD_LENGTH) {
            return { message: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`, fieldId: 'password' };
        }
        if (password !== document.getElementById('confirm-password').value) {
            return { message: 'Passwords do not match.', fieldId: 'confirm-password' };
        }
    }
    return null;
}

function handleSubmit(event) {
    event.preventDefault();
    clearMessages();

    const problem = validateForm();
    if (problem) {
        showError(problem.message, problem.fieldId);
        return;
    }

    // Placeholder until the backend login routes exist (step 2)
    showInfo(mode === 'register'
        ? 'Form is valid. Account creation will be connected in the next step.'
        : 'Form is valid. Sign-in will be connected in the next step.');
}

function showError(message, fieldId) {
    authError.textContent = message;
    authError.style.display = 'block';
    if (fieldId) {
        const field = document.getElementById(fieldId);
        field.setAttribute('aria-invalid', 'true');
        field.focus();
    }
}

function showInfo(message) {
    authInfo.textContent = message;
    authInfo.style.display = 'block';
}

function clearMessages() {
    authError.textContent = '';
    authError.style.display = 'none';
    authInfo.textContent = '';
    authInfo.style.display = 'none';
    authForm.querySelectorAll('[aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
}
