const SETTINGS_KEY = "site-settings";

function getSettings() {
	try {
		return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
	} catch (e) {
		return {};
	}
}

function saveSettings(settings) {
	localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

// theme: "auto"（跟隨系統）/ "light" / "dark"
function applyTheme(theme) {
	const html = document.documentElement;
	if (theme === "dark" || theme === "light") html.setAttribute("data-theme", theme);
	else html.removeAttribute("data-theme");
}

function setTheme(theme) {
	applyTheme(theme);
	const settings = getSettings();
	settings.theme = theme;
	delete settings.palette;
	saveSettings(settings);
	updateActiveTheme(theme);
}

function setFontZh(value) {
	document.documentElement.setAttribute("data-font-zh", value);
	const settings = getSettings();
	settings.fontZh = value;
	saveSettings(settings);
}

function setFontEn(value) {
	document.documentElement.setAttribute("data-font-en", value);
	const settings = getSettings();
	settings.fontEn = value;
	saveSettings(settings);
}

function adjustFontSize(step) {
	const settings = getSettings();
	let scale = settings.fontScale || 100;
	scale = Math.min(140, Math.max(80, scale + step * 10));
	settings.fontScale = scale;
	saveSettings(settings);
	document.documentElement.style.fontSize = scale + "%";
	document.getElementById("font-size-display").textContent = scale + "%";
}

function updateActiveTheme(theme) {
	document.querySelectorAll(".theme-btn").forEach(btn => {
		const on = btn.dataset.theme === theme;
		btn.classList.toggle("active", on);
		btn.setAttribute("aria-pressed", on ? "true" : "false");
	});
}

function toggleSettings() {
	document.getElementById("settings-overlay").classList.toggle("open");
}

function closeSettingsOnOverlay(e) {
	if (e.target === document.getElementById("settings-overlay")) {
		document.getElementById("settings-overlay").classList.remove("open");
	}
}

document.addEventListener("DOMContentLoaded", () => {
	const settings = getSettings();
	// 舊版存的是 6 選 1 的 palette，第一次載入時遷移成 light / dark
	let theme = settings.theme;
	if (!theme) {
		theme = settings.palette ? (settings.palette === "night" ? "dark" : "light") : "auto";
		settings.theme = theme;
		delete settings.palette;
		saveSettings(settings);
	}
	updateActiveTheme(theme);
	if (settings.fontZh) document.getElementById("font-zh-select").value = settings.fontZh;
	if (settings.fontEn) document.getElementById("font-en-select").value = settings.fontEn;
	document.getElementById("font-size-display").textContent = (settings.fontScale || 100) + "%";
});

document.addEventListener("keydown", e => {
	if (e.key === "Escape") document.getElementById("settings-overlay").classList.remove("open");
});
