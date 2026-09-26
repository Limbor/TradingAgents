try {
  const theme = window.localStorage.getItem("tradingagents.theme");
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  }
} catch {
  // The system preference remains available when storage is blocked.
}
