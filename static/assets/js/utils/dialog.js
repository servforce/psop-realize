export function trapDialogFocus(dialog, event) {
  const controls = [...dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
    .filter(element => element.tabIndex >= 0 && !element.disabled && element.checkVisibility());
  const first = controls[0], last = controls.at(-1);
  if (!first) { event.preventDefault(); return; }
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault(); first.focus();
  }
}
