import { useEffect, useRef, useState } from "react";

export interface DropdownOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
}
export interface DropdownGroup {
  label: string;
  options: DropdownOption[];
}

/**
 * A single-choice dropdown whose list always opens downward, below the button.
 * (A native <select> lets the browser open it upward near the bottom of the
 * window.) Listbox semantics: the button reports the expanded state, options
 * are role="option", and the arrow keys, Enter, Escape and Tab work.
 *
 * With `searchable`, the open list starts with a text box that narrows the
 * options by label, hint or value. Only listed options can be chosen; the
 * typed text is never a value.
 */
export function Dropdown({
  id,
  ariaLabel,
  value,
  groups,
  placeholder,
  disabled,
  invalid,
  searchable,
  onChange,
}: {
  id: string;
  ariaLabel: string;
  value: string;
  groups: DropdownGroup[];
  placeholder: string;
  disabled?: boolean;
  invalid?: boolean;
  searchable?: boolean;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const all = groups.flatMap((g) => g.options);
  const q = query.trim().toLowerCase();
  const matches = (o: DropdownOption) =>
    !q || [o.label, o.hint ?? "", o.value].some((t) => t.toLowerCase().includes(q));
  const shown = groups
    .map((g) => ({ ...g, options: g.options.filter(matches) }))
    .filter((g) => g.options.length);
  const enabled = shown.flatMap((g) => g.options).filter((o) => !o.disabled);
  const current = all.find((o) => o.value === value);

  // The list always opens below; if that runs past the window, scroll the page
  // just enough to show it rather than flipping it upward.
  useEffect(() => {
    if (open) list.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function show() {
    setQuery("");
    setActive(current && !current.disabled ? current.value : enabled[0]?.value ?? null);
    setOpen(true);
  }
  function choose(o: DropdownOption) {
    if (o.disabled) return;
    onChange(o.value);
    setOpen(false);
    button.current?.focus();
  }
  function move(step: number) {
    if (!enabled.length) return;
    const i = enabled.findIndex((o) => o.value === active);
    setActive(enabled[(i + step + enabled.length) % enabled.length].value);
  }

  function onKey(e: React.KeyboardEvent) {
    if (disabled) return;
    // a space typed into the search box is text, not a selection
    if (e.key === " " && (e.target as HTMLElement).tagName === "INPUT") return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        show();
      }
      return;
    }
    if (e.key === "ArrowDown") (e.preventDefault(), move(1));
    else if (e.key === "ArrowUp") (e.preventDefault(), move(-1));
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      // after typing a search, Enter takes the first match
      const o = enabled.find((x) => x.value === active) ?? (q ? enabled[0] : undefined);
      if (o) choose(o);
    } else if (e.key === "Escape") (e.preventDefault(), setOpen(false));
    else if (e.key === "Tab") setOpen(false);
  }

  return (
    <div className="dd" ref={root} onKeyDown={onKey}>
      <button
        ref={button}
        id={id}
        type="button"
        className={`dd-btn${open ? " open" : ""}${invalid ? " err" : ""}`}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
      >
        <span className={current ? "" : "dd-ph"}>{current ? current.label : placeholder}</span>
        <span className="dd-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open ? (
        <div ref={list} className="dd-list" id={`${id}-list`} role="listbox" aria-label={ariaLabel}>
          {searchable ? (
            <input
              className="dd-search"
              aria-label={`Search ${ariaLabel.toLowerCase()}`}
              placeholder="Type to search"
              autoFocus
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(null);
              }}
            />
          ) : null}
          {searchable && !shown.length ? <div className="dd-none">No matches</div> : null}
          {shown.map((g) => (
            <div key={g.label} role="group" aria-label={g.label}>
              <div className="dd-group">{g.label}</div>
              {g.options.map((o) => (
                <div
                  key={o.value}
                  role="option"
                  aria-selected={o.value === value}
                  aria-disabled={o.disabled || undefined}
                  className={`dd-opt${o.value === active ? " active" : ""}${o.value === value ? " sel" : ""}${o.disabled ? " dis" : ""}`}
                  onMouseEnter={() => !o.disabled && setActive(o.value)}
                  onClick={() => choose(o)}
                >
                  <span className="dd-check" aria-hidden="true">
                    {o.value === value ? "✓" : ""}
                  </span>
                  <span className="dd-text">
                    <span className="dd-label">{o.label}</span>
                    {o.hint ? <span className="dd-hint">{o.hint}</span> : null}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
