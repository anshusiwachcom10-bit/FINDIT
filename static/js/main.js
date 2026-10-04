document.addEventListener("DOMContentLoaded", () => {
    const homePage = document.querySelector(".home-landing");
    const menuButton = document.querySelector(".mobile-menu-toggle");
    const nav = document.querySelector(".main-nav");
    const actions = document.querySelector(".nav-actions");

    if (menuButton && nav) {
        menuButton.addEventListener("click", () => {
            const isOpen = menuButton.getAttribute("aria-expanded") === "true";
            menuButton.setAttribute("aria-expanded", String(!isOpen));
            nav.classList.toggle("mobile-open", !isOpen);
            if (actions) {
                actions.classList.toggle("mobile-open", !isOpen);
            }
        });
    }

    if (homePage) {
        document.body.classList.add("home-reveal-ready");
        const revealItems = homePage.querySelectorAll("[data-reveal]");
        if ("IntersectionObserver" in window) {
            const revealObserver = new IntersectionObserver((entries, observer) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("is-visible");
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.12, rootMargin: "0px 0px -32px 0px" });
            revealItems.forEach((item) => revealObserver.observe(item));
        } else {
            revealItems.forEach((item) => item.classList.add("is-visible"));
        }

        const recentGrid = homePage.querySelector("[data-home-items]");
        const recentFeedback = homePage.querySelector("[data-home-feedback]");
        const placeholder = () => {
            const fallback = document.createElement("span");
            fallback.className = "home-item-placeholder";
            const wordmark = document.createElement("strong");
            wordmark.textContent = "FINDIT";
            const note = document.createElement("small");
            note.textContent = "No photo provided";
            fallback.append(wordmark, note);
            return fallback;
        };
        const displayDate = (value) => {
            if (!value) return "Date not provided";
            const parsed = new Date(`${value}T00:00:00`);
            return Number.isNaN(parsed.getTime())
                ? value
                : parsed.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
        };
        const createRecentItem = (item) => {
            const article = document.createElement("article");
            article.className = "home-item-card";
            article.dataset.reveal = "";
            article.classList.add("is-visible");

            const imageLink = document.createElement("a");
            imageLink.className = "home-item-image";
            imageLink.href = `/items/${encodeURIComponent(item.id)}`;
            imageLink.tabIndex = -1;
            imageLink.setAttribute("aria-hidden", "true");
            if (item.image_url) {
                const image = document.createElement("img");
                image.src = item.image_url;
                image.alt = "";
                image.loading = "lazy";
                image.addEventListener("error", () => image.replaceWith(placeholder()), { once: true });
                imageLink.append(image);
            } else {
                imageLink.append(placeholder());
            }

            const body = document.createElement("div");
            body.className = "home-item-copy";
            const meta = document.createElement("div");
            meta.className = "home-item-meta";
            const status = document.createElement("span");
            status.className = `status ${item.type === "found" ? "found" : "lost"}`;
            status.textContent = item.type === "found" ? "Found" : "Lost";
            const date = document.createElement("time");
            date.textContent = displayDate(item.event_date);
            meta.append(status, date);

            const category = document.createElement("span");
            category.className = "home-item-category";
            category.textContent = item.category || "Uncategorized";
            const title = document.createElement("h3");
            const titleLink = document.createElement("a");
            titleLink.href = `/items/${encodeURIComponent(item.id)}`;
            titleLink.textContent = item.title || "Reported item";
            title.append(titleLink);
            const location = document.createElement("p");
            location.textContent = item.location || "Location not provided";
            body.append(meta, category, title, location);

            if (item.matches?.length) {
                const match = document.createElement("span");
                match.className = "home-item-match";
                match.textContent = `${item.matches[0].level} · ${item.matches[0].confidence}%`;
                body.append(match);
            }

            const details = document.createElement("a");
            details.className = "home-card-link";
            details.href = `/items/${encodeURIComponent(item.id)}`;
            details.append(document.createTextNode("View Details "));
            const arrow = document.createElement("span");
            arrow.setAttribute("aria-hidden", "true");
            arrow.textContent = "↗";
            details.append(arrow);
            body.append(details);
            article.append(imageLink, body);
            return article;
        };
        const createRecentEmptyState = () => {
            const empty = document.createElement("div");
            empty.className = "home-recent-empty";
            const eyebrow = document.createElement("span");
            eyebrow.className = "eyebrow";
            eyebrow.textContent = "A fresh start";
            const title = document.createElement("h3");
            title.textContent = "No items reported yet.";
            const description = document.createElement("p");
            description.textContent = "Be the first to help someone find what they lost.";
            const link = document.createElement("a");
            link.className = "btn btn-primary";
            link.href = "/report/lost";
            link.append(document.createTextNode("Report an Item "));
            const arrow = document.createElement("span");
            arrow.setAttribute("aria-hidden", "true");
            arrow.textContent = "→";
            link.append(arrow);
            empty.append(eyebrow, title, description, link);
            return empty;
        };
        if (recentGrid) {
            recentGrid.setAttribute("aria-busy", "true");
            fetch("/api/items", { headers: { Accept: "application/json" }, credentials: "same-origin" })
                .then(async (response) => {
                    const payload = await response.json();
                    if (!response.ok) {
                        throw new Error(payload.error || "Recently reported items could not be loaded.");
                    }
                    if (!Array.isArray(payload.items)) {
                        throw new Error("Recently reported items could not be loaded.");
                    }
                    const items = payload.items.slice(0, 6);
                    if (items.length) {
                        recentGrid.replaceChildren(...items.map(createRecentItem));
                    } else {
                        recentGrid.replaceChildren(createRecentEmptyState());
                    }
                    if (recentFeedback) recentFeedback.textContent = "";
                })
                .catch((error) => {
                    if (recentFeedback) {
                        recentFeedback.textContent = error.message || "Recently reported items could not be loaded.";
                        recentFeedback.classList.add("is-error");
                    }
                })
                .finally(() => recentGrid.removeAttribute("aria-busy"));
        }
    }

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const searchForm = document.querySelector("[data-search-form]");
    const itemGrid = document.querySelector("[data-item-grid]");
    const searchFeedback = document.querySelector("[data-search-feedback]");
    const resultsSummary = document.querySelector("[data-results-summary]");

    const setFeedback = (element, message, state = "") => {
        if (!element) return;
        element.textContent = message;
        element.className = `inline-feedback${state ? ` ${state}` : ""}`;
    };

    const placeholder = () => {
        const box = document.createElement("div");
        box.className = "item-image-placeholder";
        box.setAttribute("role", "img");
        box.setAttribute("aria-label", "No photo provided");
        const wordmark = document.createElement("span");
        wordmark.textContent = "FINDIT";
        const note = document.createElement("small");
        note.textContent = "No photo provided";
        box.append(wordmark, note);
        return box;
    };

    const createItemCard = (item) => {
        const article = document.createElement("article");
        article.className = "editorial-card item-card";
        article.dataset.itemCard = "";

        if (item.image_url) {
            const image = document.createElement("img");
            image.src = item.image_url;
            image.alt = item.title || "Reported item";
            image.loading = "lazy";
            image.dataset.itemImage = "";
            image.addEventListener("error", () => image.replaceWith(placeholder()), { once: true });
            article.append(image);
        } else {
            article.append(placeholder());
        }

        const body = document.createElement("div");
        body.className = "card-body";
        const meta = document.createElement("div");
        meta.className = "meta-row";
        const status = document.createElement("span");
        status.className = `status ${item.type}`;
        status.textContent = item.type === "found" ? "Found" : "Lost";
        const date = document.createElement("span");
        date.className = "date";
        date.textContent = item.event_date || "Date not provided";
        meta.append(status, date);

        const category = document.createElement("span");
        category.className = "pill neutral";
        category.textContent = item.category || "Uncategorized";
        const title = document.createElement("h3");
        title.textContent = item.title;
        const description = document.createElement("p");
        description.textContent = `${item.location || "Location not provided"} · ${item.description || "No description provided."}`;
        const footer = document.createElement("div");
        footer.className = "card-footer";
        const itemStatus = document.createElement("span");
        itemStatus.className = "item-status";
        itemStatus.textContent = item.status ? item.status[0].toUpperCase() + item.status.slice(1) : "Open";
        footer.append(itemStatus);
        if (item.matches?.length) {
            const match = document.createElement("strong");
            match.textContent = `${item.matches[0].level} · ${item.matches[0].confidence}%`;
            footer.append(match);
        }

        const details = document.createElement("a");
        details.className = "text-link";
        details.href = `/items/${encodeURIComponent(item.id)}`;
        details.textContent = "View Details →";
        body.append(meta, category, title, description, footer, details);
        article.append(body);
        return article;
    };

    const searchItems = async () => {
        if (!searchForm || !itemGrid) return;
        const params = new URLSearchParams(new FormData(searchForm));
        for (const [key, value] of params.entries()) {
            if (!value.trim()) params.delete(key);
        }
        itemGrid.setAttribute("aria-busy", "true");
        setFeedback(searchFeedback, "Searching reports…", "loading");

        try {
            const response = await fetch(`/api/items?${params.toString()}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin"
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || "We couldn’t load the reports. Please try again.");
            }

            const cards = payload.items.map(createItemCard);
            if (cards.length === 0) {
                const empty = document.createElement("div");
                empty.className = "empty-state";
                const heading = document.createElement("h3");
                heading.textContent = "No results found";
                const explanation = document.createElement("p");
                explanation.textContent = "Try changing your search or filters.";
                empty.append(heading, explanation);
                cards.push(empty);
            }
            itemGrid.replaceChildren(...cards);
            resultsSummary.textContent = `${payload.items.length} report${payload.items.length === 1 ? "" : "s"} found`;
            setFeedback(searchFeedback, "");
        } catch (error) {
            setFeedback(searchFeedback, error.message || "We couldn’t load the reports. Please try again.", "error");
        } finally {
            itemGrid.removeAttribute("aria-busy");
        }
    };

    if (searchForm && itemGrid) {
        searchForm.addEventListener("submit", (event) => {
            event.preventDefault();
            searchItems();
        });

        let debounce;
        searchForm.elements.q.addEventListener("input", () => {
            window.clearTimeout(debounce);
            debounce = window.setTimeout(searchItems, 250);
        });
        searchForm.querySelectorAll("select, input[type='date']").forEach((control) => {
            control.addEventListener("change", searchItems);
        });
        searchForm.querySelector("[data-clear-search]").addEventListener("click", () => {
            searchForm.elements.q.value = "";
            searchForm.elements.q.focus();
            searchItems();
        });
        searchForm.querySelector("[data-reset-filters]").addEventListener("click", () => {
            searchForm.reset();
            searchItems();
        });
        searchItems();
    }

    document.querySelectorAll("[data-item-image]").forEach((image) => {
        image.addEventListener("error", () => image.replaceWith(placeholder()), { once: true });
    });

    const imageInput = document.querySelector("#item-image");
    const imageName = document.querySelector("[data-image-name]");
    if (imageInput && imageName) {
        imageInput.addEventListener("change", () => {
            imageName.textContent = imageInput.files?.[0]?.name || "Choose a clear image of the item";
        });
    }

    const reportForm = document.querySelector("[data-report-form]");
    if (reportForm) {
        const feedback = reportForm.querySelector("[data-report-feedback]");
        const submitButton = reportForm.querySelector("[data-submit-label]");
        const successPanel = document.querySelector("[data-report-success]");

        reportForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!reportForm.reportValidity()) return;

            const originalLabel = submitButton.textContent;
            submitButton.disabled = true;
            submitButton.textContent = "Submitting report…";
            setFeedback(feedback, "Saving your report securely…", "loading");

            try {
                const response = await fetch(reportForm.action, {
                    method: "POST",
                    body: new FormData(reportForm),
                    headers: { "X-CSRF-Token": csrfToken, Accept: "application/json" },
                    credentials: "same-origin"
                });
                const payload = await response.json();
                if (response.status === 401) {
                    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
                    return;
                }
                if (!response.ok) {
                    throw new Error(payload.error || "Your report couldn’t be saved. Please try again.");
                }

                reportForm.hidden = true;
                successPanel.hidden = false;
                successPanel.replaceChildren();
                const heading = document.createElement("h2");
                heading.textContent = payload.item.type === "lost"
                    ? "Your lost item has been reported."
                    : "Thank you for helping return a lost item.";
                const summary = document.createElement("p");
                summary.textContent = `${payload.item.title} · Report #${payload.item.id} · ${payload.item.status}`;
                const next = document.createElement("p");
                next.textContent = payload.matches.length
                    ? `${payload.matches.length} potential match${payload.matches.length === 1 ? "" : "es"} found from the current reports.`
                    : "We’ll compare this report with new items as they’re reported.";
                const detailLink = document.createElement("a");
                detailLink.className = "btn btn-primary";
                detailLink.href = `/items/${encodeURIComponent(payload.item.id)}`;
                detailLink.textContent = "View My Report →";
                const matchesLink = document.createElement("a");
                matchesLink.className = "btn btn-secondary";
                matchesLink.href = "/matches";
                matchesLink.textContent = "Find Potential Matches →";
                const links = document.createElement("div");
                links.className = "inline-actions";
                links.append(detailLink, matchesLink);
                successPanel.append(heading, summary, next, links);
                successPanel.focus();
            } catch (error) {
                setFeedback(feedback, error.message || "Your report couldn’t be saved. Please try again.", "error");
            } finally {
                submitButton.disabled = false;
                submitButton.textContent = originalLabel;
            }
        });
    }

    document.querySelectorAll("[data-match-status-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const status = form.elements.status.value;
            const button = form.querySelector("button[type='submit']");
            const feedback = form.querySelector("[data-match-status-feedback]");
            const originalLabel = button.textContent;
            button.disabled = true;
            button.textContent = "Saving…";
            setFeedback(feedback, "Updating match status…", "loading");

            try {
                const response = await fetch(form.action, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfToken,
                        Accept: "application/json"
                    },
                    credentials: "same-origin",
                    body: JSON.stringify({ status })
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.error || "The match status couldn’t be updated.");
                }
                setFeedback(
                    feedback,
                    `Match status saved as ${payload.match.status.replace(/\b\w/g, (letter) => letter.toUpperCase())}.`,
                    "success"
                );
                const statusLabel = form.closest(".potential-match-card")
                    ?.querySelector(".match-card-header .eyebrow");
                if (statusLabel) {
                    statusLabel.textContent = `${payload.match.level} · ${payload.match.status.replace(/\b\w/g, (letter) => letter.toUpperCase())}`;
                }
            } catch (error) {
                setFeedback(feedback, error.message || "The match status couldn’t be updated.", "error");
            } finally {
                button.disabled = false;
                button.textContent = originalLabel;
            }
        });
    });
});
