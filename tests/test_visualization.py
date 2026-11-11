import mido

from conductor_main.visualization import visualize_midi_plotly


def test_note_hover_keeps_start_and_duration_for_side_selection():
    midi = mido.MidiFile()
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.Message("note_on", note=60, velocity=100, time=480),
                mido.Message("note_off", note=60, time=480),
                mido.Message("note_on", note=62, velocity=79, time=6360),
                mido.Message("note_off", note=62, time=360),
            ]
        )
    )

    figure = visualize_midi_plotly(midi)
    notes = figure.data[0]
    assert list(notes.base) == [4, 61]
    assert list(notes.x) == [4, 3]
    assert list(notes.y) == [60, 62]
    assert "Bar 4, Beat 4.2" in notes.hovertext[1]
    assert "Duration: 3/16" in notes.hovertext[1]
