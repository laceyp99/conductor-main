"""Plotly piano-roll visualization owned by the Gradio client."""

import mido
import plotly.graph_objects as go

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BACKGROUND = "#1a1a2e"
BLACK_KEY_BACKGROUND = "#252540"
GRID = "#2a2a4a"
BAR_GRID = "#4a4a6a"
TEXT = "#e0e0e0"


def _is_black_key(pitch):
    return pitch % 12 in {1, 3, 6, 8, 10}


def _velocity_color(velocity):
    amount = velocity / 127.0
    red = int(45 + amount * 210)
    green = int(27 + amount * 80)
    blue = int(78 + amount * 13)
    return f"rgb({red},{green},{blue})"


def _note_label(pitch):
    return NOTE_NAMES[pitch % 12], (pitch // 12) - 1


def _duration_label(sixteenths):
    labels = {1: "1/16", 2: "1/8", 4: "1/4", 8: "1/2", 16: "1 bar"}
    value = int(sixteenths)
    return labels.get(value, f"{value}/16")


def visualize_midi_plotly(input_midi):
    """Render a MIDI file or ``MidiFile`` as a four-bar piano roll."""
    if isinstance(input_midi, str):
        midi = mido.MidiFile(input_midi)
    elif isinstance(input_midi, mido.MidiFile):
        midi = input_midi
    else:
        raise ValueError("Input must be a filename or a MidiFile object")

    elapsed = 0
    active = {}
    notes = []
    for message in mido.merge_tracks(midi.tracks):
        elapsed += message.time
        if message.type == "note_on" and message.velocity > 0:
            active.setdefault(message.note, []).append((elapsed, message.velocity))
        elif (
            message.type == "note_off"
            or (message.type == "note_on" and message.velocity == 0)
        ) and active.get(message.note):
            start, velocity = active[message.note].pop(0)
            notes.append((message.note, start, elapsed, velocity))

    sixteenth_notes = [
        (
            pitch,
            start / midi.ticks_per_beat * 4,
            end / midi.ticks_per_beat * 4,
            velocity,
        )
        for pitch, start, end, velocity in notes
    ]
    figure = go.Figure()
    if not sixteenth_notes:
        figure.add_annotation(
            text="No notes found",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return figure

    pitches = [note[0] for note in sixteenth_notes]
    minimum = max(0, min(pitches) - 1)
    maximum = min(127, max(pitches) + 1)

    for pitch in range(minimum, maximum + 1):
        if _is_black_key(pitch):
            figure.add_shape(
                type="rect",
                x0=0,
                x1=64,
                y0=pitch - 0.5,
                y1=pitch + 0.5,
                fillcolor=BLACK_KEY_BACKGROUND,
                line={"width": 0},
                layer="below",
            )
    for beat in range(1, 16):
        if beat % 4:
            position = beat * 4
            figure.add_shape(
                type="line",
                x0=position,
                x1=position,
                y0=minimum - 0.5,
                y1=maximum + 0.5,
                line={"color": GRID, "width": 1},
                layer="below",
            )
    for bar in range(5):
        position = bar * 16
        figure.add_shape(
            type="line",
            x0=position,
            x1=position,
            y0=minimum - 0.5,
            y1=maximum + 0.5,
            line={"color": BAR_GRID, "width": 2},
            layer="below",
        )
    for bar in range(1, 5):
        figure.add_annotation(
            x=(bar - 1) * 16 + 8,
            y=maximum + 1,
            text=f"Bar {bar}",
            showarrow=False,
            font={"color": TEXT, "size": 12},
            yanchor="bottom",
        )

    colors, hover_text = [], []
    for pitch, start, end, velocity in sixteenth_notes:
        colors.append(_velocity_color(velocity))
        bar = int(start // 16) + 1
        beat = int((start % 16) // 4) + 1
        subdivision = int(start % 4) + 1
        name, octave = _note_label(pitch)
        hover_text.append(
            f"Note: {name}{octave}<br>Velocity: {velocity}<br>"
            f"Position: Bar {bar}, Beat {beat}.{subdivision}<br>"
            f"Duration: {_duration_label(end - start)}"
        )

    figure.add_trace(
        go.Bar(
            orientation="h",
            base=[start for _, start, _, _ in sixteenth_notes],
            x=[end - start for _, start, end, _ in sixteenth_notes],
            y=pitches,
            width=0.8,
            marker={
                "color": colors,
                "line": {"color": "rgba(255,255,255,0.3)", "width": 1},
            },
            hovertext=hover_text,
            hovertemplate="%{hovertext}<extra></extra>",
            hoverlabel={"bgcolor": GRID, "font_size": 12, "font_color": TEXT},
        )
    )
    ticks = [pitch for pitch in range(minimum, maximum + 1) if pitch % 12 == 0]
    labels = [f"{name}{octave}" for name, octave in map(_note_label, ticks)]
    figure.update_layout(
        plot_bgcolor=BACKGROUND,
        paper_bgcolor=BACKGROUND,
        xaxis={
            "range": [0, 64],
            "showgrid": False,
            "zeroline": False,
            "tickmode": "array",
            "tickvals": [0, 16, 32, 48, 64],
            "ticktext": ["", "", "", "", ""],
            "fixedrange": False,
        },
        yaxis={
            "range": [minimum - 0.5, maximum + 1.5],
            "showgrid": False,
            "zeroline": False,
            "tickmode": "array",
            "tickvals": ticks,
            "ticktext": labels,
            "tickfont": {"color": TEXT, "size": 10},
        },
        showlegend=False,
        barmode="overlay",
        margin={"l": 60, "r": 20, "t": 40, "b": 40},
        height=400,
        hoverdistance=20,
        hovermode="closest",
        modebar={"bgcolor": "rgba(0,0,0,0)", "color": TEXT, "activecolor": "#FF6B5B"},
    )
    return figure


# Install once on the Gradio plot. Plotly still handles full-bar hit testing;
# this overlay places the existing text without its automatic side switching.
PIANO_ROLL_HOVER_JS = """
() => {
    const root = document.querySelector("#piano-roll");
    if (!root || root.dataset.noteHoverInstalled) return;
    root.dataset.noteHoverInstalled = "true";
    root.style.position = "relative";
    const style = document.createElement("style");
    style.textContent = "#piano-roll .hoverlayer { visibility: hidden; }";
    root.append(style);
    const tip = document.createElement("div");
    tip.className = "note-hover-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.style.cssText = "position:absolute;display:none;pointer-events:none;" +
        "z-index:20;white-space:pre-line;padding:4px 6px;border:1px solid;" +
        "font:12px Arial,sans-serif;line-height:1.3;max-width:calc(100% - 16px)";
    root.append(tip);
    const bound = new WeakSet();
    const bind = () => {
        const plot = root.querySelector(".js-plotly-plot");
        if (!plot?.on || bound.has(plot)) return;
        bound.add(plot);
        tip.style.display = "none";
        let active = null;
        const hide = () => { active = null; tip.style.display = "none"; };
        const place = () => {
            if (!active || !plot.isConnected) return hide();
            const note = plot.querySelectorAll(".barlayer .point path")[active.pointNumber];
            if (!note) return hide();
            const text = active.data.hovertext?.[active.pointNumber];
            if (!text) return hide();
            tip.textContent = text.replaceAll("<br>", "\\n");
            const label = active.fullData.hoverlabel;
            tip.style.background = label.bgcolor;
            tip.style.color = label.font.color;
            tip.style.borderColor = label.font.color;
            tip.style.display = "block";
            const box = note.getBoundingClientRect();
            const container = root.getBoundingClientRect();
            const width = tip.offsetWidth, height = tip.offsetHeight;
            const start = active.data.base[active.pointNumber];
            let left = start < 32 ? box.right - container.left + 8 :
                box.left - container.left - width - 8;
            let top = (box.top + box.bottom) / 2 - container.top - height / 2;
            // A spanning note may leave no space on its chosen side.
            // Move above/below it instead of covering it or clipping the text.
            if (left < 8 || left + width > container.width - 8) {
                left = Math.max(8, Math.min(left, container.width - width - 8));
                top = box.top - container.top - height - 8;
                if (top < 8) top = box.bottom - container.top + 8;
            }
            tip.style.left = left + "px";
            tip.style.top = Math.max(8, Math.min(top, container.height - height - 8)) + "px";
        };
        plot.on("plotly_hover", event => {
            active = event.points[0];
            place();
        });
        plot.on("plotly_unhover", hide);
        plot.on("plotly_relayout", place);
        plot.on("plotly_afterplot", place);
        root.addEventListener("mouseleave", hide);
        new ResizeObserver(place).observe(plot);
    };
    new MutationObserver(bind).observe(root, {childList: true, subtree: true});
    bind();
}
"""
