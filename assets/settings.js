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

function setPalette(palette) {
	document.documentElement.setAttribute("data-palette", palette);
	const settings = getSettings();
	settings.palette = palette;
	saveSettings(settings);
	updateActiveSwatch(palette);
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

function updateActiveSwatch(palette) {
	document.querySelectorAll(".swatch-btn").forEach(btn => {
		btn.classList.toggle("active", btn.dataset.palette === palette);
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
	updateActiveSwatch(settings.palette || "coffee");
	if (settings.fontZh) document.getElementById("font-zh-select").value = settings.fontZh;
	if (settings.fontEn) document.getElementById("font-en-select").value = settings.fontEn;
	document.getElementById("font-size-display").textContent = (settings.fontScale || 100) + "%";
});

document.addEventListener("keydown", e => {
	if (e.key === "Escape") document.getElementById("settings-overlay").classList.remove("open");
});
