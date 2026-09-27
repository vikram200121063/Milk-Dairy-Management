// Floating "Business Assistant" chat widget. Talks to POST /ai/ask and
// POST /ai/reset. Degrades quietly: if those routes aren't present (or
// AI isn't configured) the widget just isn't rendered at all - see
// app/templates/partials/ai_assistant_widget.html.
(function () {
    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function inlineFormat(str) {
        // **bold** and *italic* only - enough for how the assistant
        // actually writes, without pulling in a full markdown library.
        return str.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>");
    }

    // Turns the assistant's markdown-ish replies (bold, "* " bullet lists,
    // paragraphs) into safe HTML - escaping first so nothing it writes can
    // inject markup, then layering just enough formatting back on top.
    function renderRichText(text) {
        var lines = escapeHtml(text).split(/\r?\n/);
        var html = "";
        var inList = false;

        lines.forEach(function (line) {
            var trimmed = line.trim();
            var bulletMatch = trimmed.match(/^[*-]\s+(.*)$/);

            if (bulletMatch) {
                if (!inList) {
                    html += "<ul>";
                    inList = true;
                }
                html += "<li>" + inlineFormat(bulletMatch[1]) + "</li>";
                return;
            }

            if (inList) {
                html += "</ul>";
                inList = false;
            }
            if (trimmed !== "") {
                html += "<p>" + inlineFormat(trimmed) + "</p>";
            }
        });

        if (inList) {
            html += "</ul>";
        }
        return html;
    }

    document.addEventListener("DOMContentLoaded", function () {
        var toggle = document.getElementById("ai-widget-toggle");
        var panel = document.getElementById("ai-widget-panel");
        var closeBtn = document.getElementById("ai-widget-close");
        var resetBtn = document.getElementById("ai-widget-reset");
        var expandBtn = document.getElementById("ai-widget-expand");
        var form = document.getElementById("ai-widget-form");
        var input = document.getElementById("ai-widget-input");
        var messages = document.getElementById("ai-widget-messages");

        if (!toggle || !panel || !form) {
            return;
        }

        function addMessage(text, cssClass, rich) {
            var el = document.createElement("div");
            el.className = "ai-widget-msg " + cssClass;
            if (rich) {
                el.innerHTML = renderRichText(text);
            } else {
                el.textContent = text;
            }
            messages.appendChild(el);
            messages.scrollTop = messages.scrollHeight;
            return el;
        }

        function setExpanded(expanded) {
            panel.classList.toggle("is-expanded", expanded);
            expandBtn.setAttribute("title", expanded ? "Shrink" : "Expand");
            expandBtn.setAttribute("aria-label", expanded ? "Shrink" : "Expand");
            try {
                localStorage.setItem("aiWidgetExpanded", expanded ? "1" : "0");
            } catch (e) {
                // Private browsing / blocked storage - the toggle still works
                // for this page view, it just won't be remembered next time.
            }
        }

        if (expandBtn) {
            var startExpanded = false;
            try {
                startExpanded = localStorage.getItem("aiWidgetExpanded") === "1";
            } catch (e) {
                // ignore - default to compact
            }
            setExpanded(startExpanded);

            expandBtn.addEventListener("click", function () {
                setExpanded(!panel.classList.contains("is-expanded"));
            });
        }

        toggle.addEventListener("click", function () {
            panel.classList.toggle("d-none");
            if (!panel.classList.contains("d-none")) {
                input.focus();
            }
        });

        closeBtn.addEventListener("click", function () {
            panel.classList.add("d-none");
        });

        resetBtn.addEventListener("click", function () {
            fetch("/ai/reset", { method: "POST" }).catch(function () {});
            messages.innerHTML = "";
            addMessage(
                "New conversation started. Ask me about customers, buyers, payments, or your finances.",
                "ai-widget-msg-assistant",
                false
            );
        });

        form.addEventListener("submit", function (evt) {
            evt.preventDefault();
            var question = input.value.trim();
            if (!question) {
                return;
            }

            addMessage(question, "ai-widget-msg-user", false);
            input.value = "";
            input.disabled = true;
            var submitBtn = form.querySelector("button[type=submit]");
            if (submitBtn) submitBtn.disabled = true;
            var pending = addMessage("Thinking...", "ai-widget-msg-pending", false);

            fetch("/ai/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question: question }),
            })
                .then(function (resp) {
                    return resp.json().then(function (data) {
                        return { ok: resp.ok, data: data };
                    });
                })
                .then(function (result) {
                    pending.remove();
                    if (result.data && result.data.answer) {
                        addMessage(result.data.answer, "ai-widget-msg-assistant", true);
                    } else {
                        addMessage(
                            (result.data && result.data.error) || "Something went wrong. Please try again.",
                            "ai-widget-msg-error",
                            false
                        );
                    }
                })
                .catch(function () {
                    pending.remove();
                    addMessage(
                        "Couldn't reach the assistant. Check your connection and try again.",
                        "ai-widget-msg-error",
                        false
                    );
                })
                .finally(function () {
                    input.disabled = false;
                    if (submitBtn) submitBtn.disabled = false;
                    input.focus();
                });
        });
    });
})();
