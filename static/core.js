// fileName: core.js
// ===== TOAST SYSTEM =====
function showToast(message, type = 'normal') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ===== MODAL SYSTEM =====
const modal = {
    overlay: null,
    title: null,
    body: null,
    inputContainer: null,
    input: null,
    btnConfirm: null,
    btnCancel: null,

    init() {
        this.overlay = document.getElementById('custom-modal');
        this.title = document.getElementById('modal-title');
        this.body = document.getElementById('modal-body');
        this.inputContainer = document.getElementById('modal-input-container');
        this.input = document.getElementById('modal-input');
        this.btnConfirm = document.getElementById('modal-confirm');
        this.btnCancel = document.getElementById('modal-cancel');
    },

    reset() {
        this.inputContainer.style.display = 'none';
        this.input.value = '';
        this.btnCancel.style.display = 'block';
        this.btnConfirm.className = 'btn-primary';
        this.btnConfirm.textContent = 'OK';

        const newConfirm = this.btnConfirm.cloneNode(true);
        const newCancel = this.btnCancel.cloneNode(true);

        this.btnConfirm.parentNode.replaceChild(newConfirm, this.btnConfirm);
        this.btnCancel.parentNode.replaceChild(newCancel, this.btnCancel);

        this.btnConfirm = newConfirm;
        this.btnCancel = newCancel;
    },

    show(options) {
        return new Promise((resolve) => {
            this.reset();

            this.title.textContent = options.title || 'Notification';
            this.body.textContent = options.message || '';
            this.overlay.classList.add('active');

            if (options.type === 'prompt') {
                this.inputContainer.style.display = 'block';
                this.input.type = options.inputType || 'text';
                this.input.placeholder = options.placeholder || '';
                this.input.focus();

                this.input.onkeydown = (e) => {
                    if (e.key === 'Enter') this.btnConfirm.click();
                };
            }

            if (options.type === 'alert') {
                this.btnCancel.style.display = 'none';
            }

            if (options.danger) {
                this.btnConfirm.className = 'btn-danger';
                this.btnConfirm.textContent = options.confirmText || 'Delete';
            }

            this.btnConfirm.onclick = () => {
                this.overlay.classList.remove('active');
                resolve(options.type === 'prompt' ? this.input.value : true);
            };

            this.btnCancel.onclick = () => {
                this.overlay.classList.remove('active');
                resolve(options.type === 'prompt' ? null : false);
            };
        });
    }
};

document.addEventListener("DOMContentLoaded", () => {
    modal.init();
    
    // Check if we are outside of the chat page OR on the main menu chat page
    if (!window.location.pathname.includes('/chat')) {
        initChatStream();
    }
});

async function customAlert(message) {
    return modal.show({ type: 'alert', message, title: 'Notice' });
}

async function customConfirm(message, title = 'Confirm Action', danger = false) {
    return modal.show({ type: 'confirm', message, title, danger, confirmText: 'Yes' });
}

async function customPrompt(title, placeholder = '', inputType = 'text') {
    return modal.show({ type: 'prompt', title, placeholder, inputType });
}

function setTheme(name) {
    document.body.className = "";
    if (name) document.body.classList.add("theme-" + name);
    localStorage.setItem("theme", name);
}

// Initialization and Error Handling
(function () {
    const saved = localStorage.getItem("theme");
    if (saved) setTheme(saved);
})();

function removeFormatting() {
    const ta = document.getElementById("post-content");
    const s = ta.selectionStart;
    const e = ta.selectionEnd;

    if (s === e) return; // nothing selected

    let selected = ta.value.substring(s, e);

    // remove all BBCode tags in selection
    selected = selected.replace(/\[\/?(?:b|i|u|s|size(?:=[^\]]+)?)\]/gi, '');

    ta.value = ta.value.substring(0, s) + selected + ta.value.substring(e);

    // Restore selection
    ta.selectionStart = s;
    ta.selectionEnd = s + selected.length;
    ta.focus();
}

function initChatStream() {
    const source = new EventSource("/api/stream_messages");

    source.onmessage = function(event) {
        const data = JSON.parse(event.data);
        
        if (data.status === "heartbeat") {
            return; 
        }

        // Check if we are inside a specific user's chat room
        if (window.location.pathname.includes('/chat')) {
            if (typeof currentTargetId !== 'undefined' && currentTargetId == data.sender) {
                if (typeof appendMessageToUI === "function") {
                    appendMessageToUI(data);
                }
            } else {
                // If on the DM list page OR chatting with someone else, show toast
                showToast(`New message from ${data.sender_username || data.sender}`);
            }
        } else {
            showToast(`New message from ${data.sender_username || data.sender}`);
        }
    };
}

function searchPeople() {
    document.body.classList.add("blurred");
    document.getElementById("search-overlay").classList.remove("hidden");
    document.getElementById("user-search-input").focus();
}

function closeSearch() {
    document.body.classList.remove("blurred");
    document.getElementById("search-overlay").classList.add("hidden");
}

async function performFuzzySearch(q) {
    const resultsDiv = document.getElementById('fuzzy-results');
    if (q.length < 1) {
        resultsDiv.innerHTML = "";
        return;
    }

    const res = await fetch(`/api/search_users?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    
    resultsDiv.innerHTML = data.users.map(u => `
        <div class="dm-item" onclick="location.href='/chat/${u.username}'">
            <div class="dm-icon">${u.username[0].toUpperCase()}</div>
            <div class="dm-details" style="margin-left: 12px;">
                <b style="color: #ffffff; display: block;">@${u.username}</b>
                <span style="font-size: 0.8em; color: var(--muted);">Click to chat</span>
            </div>
        </div>
    `).join('');
}
