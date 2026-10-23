import type { ReactNode } from "react";
import "./SliderRow.css";

// Port of `jobfinder/ui/settings.py`'s `_SliderRow` — slider + live value
// label + suffix (T25). The desktop widget updated the label in a Qt
// `valueChanged` handler; here it's just the controlled `value` prop
// re-rendering, since the label text is derived from it directly.
export function SliderRow({
  label,
  value,
  min,
  max,
  suffix = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (value: number) => void;
}): ReactNode {
  return (
    <div className="slider-row">
      <span className="slider-row-value">{`${value}${suffix}`}</span>
      <input
        type="range"
        className="slider-row-input"
        aria-label={label}
        min={min}
        max={max}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
