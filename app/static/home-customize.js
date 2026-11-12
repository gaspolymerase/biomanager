/* Customize Home (templates/home/_customize.html): the cards of Overview as
 * a small map. A card is dragged into its place (a mouse takes it anywhere,
 * a finger by its grip, the keyboard with the arrow keys on the grip), made
 * wide, taken off Home or put back on. Each card carries its own form
 * fields, so what is saved is the map as it stands; closing without saving
 * puts the map back as Home is.
 */
(function () {
  "use strict";
  const form = document.querySelector("[data-home-customize]");
  if (!form) return;
  const dialog = form.closest("dialog");
  const grid = form.querySelector("[data-home-map-grid]");
  const tray = form.querySelector("[data-home-map-tray]");
  const body = form.querySelector(".record-dialog-body");
  const said = form.querySelector("[data-home-map-said]");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!grid || !tray) return;

  const cards = () => [...form.querySelectorAll(".home-map-card")];
  const field = (card, name) => card.querySelector('input[name="' + name + '"]');
  const onHome = (card) => card.parentNode === grid;

  function say(key, card) {
    if (!said) return;
    const at = [...grid.children].indexOf(card) + 1;
    said.textContent = (form.dataset[key] || "")
      .replace("{name}", card.dataset.name)
      .replace("{position}", at)
      .replace("{total}", grid.children.length);
  }

  /* What follows from where the cards are: the fields each one sends, the
     lines for an empty map or tray, and the Counts choices, which are only
     there to choose while the Counts card is on. */
  function settle() {
    for (const card of cards()) {
      field(card, "show").checked = onHome(card);
      const wide = card.classList.contains("is-wide");
      field(card, "wide").checked = wide;
      card.querySelector("[data-map-wide]").setAttribute("aria-pressed", wide ? "true" : "false");
    }
    form.querySelector("[data-home-map-none]").hidden = grid.children.length > 0;
    form.querySelector("[data-home-map-all]").hidden = tray.children.length > 0;
    const counts = form.querySelector("[data-home-map-counts]");
    const stats = form.querySelector('.home-map-card[data-key="stats"]');
    if (counts) counts.hidden = Boolean(stats) && !onHome(stats);
  }

  /* Move cards with `change`, and let every card that ends up somewhere
     else slide there from where it was. */
  function reflow(change) {
    const before = new Map(cards().map((c) => [c, c.getBoundingClientRect()]));
    change();
    settle();
    if (still.matches) return;
    for (const [card, was] of before) {
      const now = card.getBoundingClientRect();
      const dx = was.left - now.left, dy = was.top - now.top;
      if ((!dx && !dy) || card.classList.contains("is-dragging")) continue;
      card.animate([{ transform: "translate(" + dx + "px," + dy + "px)" }, { transform: "none" }],
        { duration: 160, easing: "cubic-bezier(.2,.7,.3,1)" });
    }
  }

  function flash(card) {
    card.classList.add("is-new");
    setTimeout(() => card.classList.remove("is-new"), 900);
  }

  /* The map as Home is now, to put back when the dialog closes unsaved. */
  const asHomeIs = {
    grid: [...grid.children], tray: [...tray.children],
    wide: new Set(cards().filter((c) => c.classList.contains("is-wide"))),
    ticks: [...form.querySelectorAll('input[type="checkbox"]')].map((box) => [box, box.checked]),
  };
  let saving = false;
  form.addEventListener("submit", (e) => { if (!e.defaultPrevented) saving = true; });
  dialog.addEventListener("close", () => {
    if (saving) return;
    drop();
    asHomeIs.grid.forEach((c) => grid.appendChild(c));
    asHomeIs.tray.forEach((c) => tray.appendChild(c));
    cards().forEach((c) => c.classList.toggle("is-wide", asHomeIs.wide.has(c)));
    asHomeIs.ticks.forEach(([box, on]) => { box.checked = on; });
    settle();
  });

  form.addEventListener("click", (e) => {
    const card = e.target.closest(".home-map-card");
    if (!card) return;
    if (e.target.closest("[data-map-wide]")) {
      reflow(() => card.classList.toggle("is-wide"));
    } else if (e.target.closest("[data-map-hide]")) {
      const next = card.nextElementSibling || card.previousElementSibling;
      reflow(() => tray.appendChild(card));
      say("saidOff", card);
      (next ? next.querySelector("[data-map-grip]") : card.querySelector("[data-map-add]")).focus();
    } else if (e.target.closest("[data-map-add]")) {
      reflow(() => grid.appendChild(card));
      flash(card);
      say("saidOn", card);
      card.querySelector("[data-map-grip]").focus();
      card.scrollIntoView({ block: "nearest" });
    }
  });

  /* The arrow keys on a card's grip: earlier, later, first, last. */
  form.addEventListener("keydown", (e) => {
    const grip = e.target.closest("[data-map-grip]");
    const card = grip && grip.closest(".home-map-card");
    if (!card || !onHome(card)) return;
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[e.key];
    if (step === -1 && card.previousElementSibling) reflow(() => grid.insertBefore(card, card.previousElementSibling));
    else if (step === 1 && card.nextElementSibling) reflow(() => grid.insertBefore(card.nextElementSibling, card));
    else if (e.key === "Home") reflow(() => grid.prepend(card));
    else if (e.key === "End") reflow(() => grid.appendChild(card));
    else if (!step) return;
    e.preventDefault();
    grip.focus();
    card.scrollIntoView({ block: "nearest" });
    say("saidPlace", card);
  });

  /* ---- dragging ---------------------------------------------------------
     `drag` is the card in hand. It stays in the map, drawn as the gap it
     will fill, and moves there as the pointer passes over the others; a
     copy of it follows the pointer. */
  let drag = null;

  form.addEventListener("pointerdown", (e) => {
    const card = e.target.closest(".home-map-card");
    if (!card || e.button !== 0 || drag) return;
    const grip = e.target.closest("[data-map-grip]");
    if (!grip && (e.target.closest("button") || e.pointerType !== "mouse")) return;
    drag = { card, pointer: e.pointerId, x: e.clientX, y: e.clientY, ghost: null, rested: 0 };
  });

  function lift(e) {
    const card = drag.card, box = card.getBoundingClientRect(), frame = dialog.getBoundingClientRect();
    const ghost = card.cloneNode(true);
    ghost.querySelectorAll("input").forEach((input) => input.remove());
    ghost.classList.add("home-map-ghost");
    ghost.style.width = box.width + "px";
    ghost.style.height = box.height + "px";
    dialog.appendChild(ghost);
    Object.assign(drag, { ghost, frame, dx: e.clientX - box.left, dy: e.clientY - box.top });
    card.classList.add("is-dragging");
    dialog.classList.add("is-arranging");
    try { card.setPointerCapture(drag.pointer); } catch (err) { /* the pointer is gone */ }
  }

  function follow(e) {
    const g = drag.ghost, f = drag.frame;
    g.style.transform = "translate(" + (e.clientX - drag.dx - f.left - dialog.clientLeft) + "px,"
      + (e.clientY - drag.dy - f.top - dialog.clientTop) + "px)";
  }

  function place(e) {
    const card = drag.card;
    const at = document.elementFromPoint(e.clientX, e.clientY);
    if (!at || !form.contains(at)) return;
    const over = at.closest(".home-map-card");
    const inTray = at.closest("[data-home-map-off]");
    if (over && over !== card && over.parentNode === grid) {
      // The card takes the place of the one under the pointer: after it
      // when coming from before, before it otherwise. A card that just
      // moved rests a moment, so the two do not swap back and forth.
      const wait = drag.rested - performance.now();
      if (wait > 0) {
        // Look again once it has rested: the pointer may stop moving here.
        clearTimeout(drag.again);
        drag.again = setTimeout(() => { if (drag && drag.card === card) place(e); }, wait + 10);
        return;
      }
      const fromBefore = onHome(card) && (card.compareDocumentPosition(over) & Node.DOCUMENT_POSITION_FOLLOWING);
      reflow(() => grid.insertBefore(card, fromBefore ? over.nextElementSibling : over));
      drag.rested = performance.now() + 180;
    } else if (inTray && onHome(card)) {
      reflow(() => tray.appendChild(card));
    } else if (!over && !inTray && !onHome(card) && at.closest(".home-map")) {
      reflow(() => grid.appendChild(card));
    }
  }

  function drop() {
    if (!drag) return;
    const { card, ghost, pointer } = drag;
    clearTimeout(drag.again);
    drag = null;
    if (!ghost) return;
    ghost.remove();
    card.classList.remove("is-dragging");
    dialog.classList.remove("is-arranging");
    try { card.releasePointerCapture(pointer); } catch (err) { /* already released */ }
  }

  window.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointer) return;
    if (!drag.ghost) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
      lift(e);
    }
    e.preventDefault();
    follow(e);
    // Near the top or bottom of the list, the list scrolls under the card.
    const view = body.getBoundingClientRect();
    if (e.clientY < view.top + 36) body.scrollTop -= 14;
    else if (e.clientY > view.bottom - 36) body.scrollTop += 14;
    place(e);
  }, { passive: false });

  function release(e) {
    if (!drag || e.pointerId !== drag.pointer) return;
    const card = drag.card, moved = Boolean(drag.ghost);
    drop();
    if (moved) say(onHome(card) ? "saidPlace" : "saidOff", card);
  }
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);

  settle();
})();
