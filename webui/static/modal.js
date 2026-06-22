/* MediaVault Console — shared destructive-confirm modal for `replace`.
 * (IMP-E14, Candidate B). ES module; `node --check modal.js` covers it.
 *
 * Preserved verbatim in behavior from the reclaim console: the modal is the
 * unmissable gate before a destructive `replace` POST. Cancel / backdrop click /
 * Esc all dismiss without acting; only the confirm button runs the callback.
 * id + path are rendered via textContent (XSS-safe).
 */

"use strict";

function $(sel) {
  return document.querySelector(sel);
}

var modalOnConfirm = null;

export function openConfirmModal(item, onConfirm) {
  modalOnConfirm = onConfirm;
  var modal = $("#modal");
  var target = $("#modal-target");
  target.textContent = "";

  var idline = document.createElement("div");
  var b = document.createElement("b");
  b.textContent = item.id;
  idline.appendChild(b);
  target.appendChild(idline);

  var pathline = document.createElement("div");
  pathline.textContent = item.path || "";
  pathline.style.marginTop = "4px";
  pathline.style.opacity = "0.8";
  target.appendChild(pathline);

  modal.hidden = false;
  modal.classList.add("show");
  // Focus the safe default (Cancel).
  $("#modal-cancel").focus();
}

function closeModal() {
  var modal = $("#modal");
  modal.classList.remove("show");
  modal.hidden = true;
  modalOnConfirm = null;
}

export function wireModal() {
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#modal-confirm").addEventListener("click", function () {
    var fn = modalOnConfirm;
    closeModal();
    if (fn) fn();
  });
  // Backdrop click cancels (but not clicks inside the dialog).
  $("#modal").addEventListener("click", function (e) {
    if (e.target === $("#modal")) closeModal();
  });
  // Esc cancels.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#modal").hidden) closeModal();
  });
}
