// Contract Clause Analyzer - colour theme (dark by default, remembered per browser).
// Loaded in <head> on every page so the saved theme applies before anything is drawn.
const THEME_STORAGE_KEY = 'theme';

function getSavedTheme() {
    try {
        return localStorage.getItem(THEME_STORAGE_KEY) === 'light' ? 'light' : 'dark';
    } catch (error) {
        return 'dark';   // storage blocked (e.g. private mode): fall back to the default
    }
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
}

function toggleTheme() {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(next);
    try {
        localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch (error) {
        // Not saved, but the switch still works for this page
    }
}

// Wires up any theme button on the page once it exists
function initThemeToggle(buttonId) {
    const button = document.getElementById(buttonId);
    if (button) button.addEventListener('click', toggleTheme);
}

applyTheme(getSavedTheme());
