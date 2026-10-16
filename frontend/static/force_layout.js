(() => {
  const params = new URLSearchParams(window.location.search);
  const context = params.get("context") || "anonymous";
  const hasSelection = ["articulation", "cote", "mouvement"].some(key => params.get(key));
  const banner = document.getElementById("flowBanner");
  if (banner) banner.hidden = !context.startsWith("patient:") && !hasSelection;
})();
