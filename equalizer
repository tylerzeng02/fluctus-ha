import streamlit as st
import numpy as np
import sounddevice as sd
import scipy.signal as signal
import threading

st.set_page_config(page_title="10-Band Hearing Aid Equalizer", layout="centered")
st.title("10-Band Hearing Aid Equalizer")
st.markdown("Choose a preset or adjust sliders manually. Then click 'Start' to begin hearing aid mode.")

frequencies = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

presets = {
    "None (Manual)":          [0.0] * 10,
    "Presbycusis":            [0, 0, 0, 0, 2, 4, 6, 10, 12, 10],
    "Low-Frequency Loss":     [8, 6, 6, 4, 2, 0, 0, 0, 0, 0],
    "Noise-Induced Loss":     [0, 0, 0, 0, 0, 0, 4, 8, 6, 4],
    "Conductive Loss":        [10] * 10,
}

selected_preset = st.selectbox("Choose a Hearing Profile Preset", list(presets.keys()))

if st.button("Load Preset into Sliders"):
    for i, freq in enumerate(frequencies):
        st.session_state[f"slider_{freq}"] = presets[selected_preset][i]

gains = []
st.subheader("Frequency Band Gains (in dB)")
for i, freq in enumerate(frequencies):
    default = presets["None (Manual)"][i]
    gain = st.slider(
        f"{freq} Hz",
        min_value=-10.0,
        max_value=60.0,
        value=st.session_state.get(f"slider_{freq}", default),
        step=1.0,
        key=f"slider_{freq}"
    )
    gains.append(gain)

st.markdown("### Current Gain Settings")
gain_display = {f"{freq} Hz": f"{g:.1f} dB" for freq, g in zip(frequencies, gains)}
st.table(gain_display)

stream = None
filters = []
running = False

def design_peaking_eq(fs, center_freq, gain_db, Q=1.0):
    A = 10 ** (gain_db / 40)
    omega = 2 * np.pi * center_freq / fs
    alpha = np.sin(omega) / (2 * Q)
    b0 = 1 + alpha * A
    b1 = -2 * np.cos(omega)
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * np.cos(omega)
    a2 = 1 - alpha / A
    b = np.array([b0, b1, b2]) / a0
    a = np.array([a0, a1, a2]) / a0
    return b, a

def create_filterbank(fs, gains):
    return [design_peaking_eq(fs, freq, gain) for freq, gain in zip(frequencies, gains)]

def callback(indata, outdata, frames, time, status):
    if status:
        print("Stream status:", status)
    processed = indata.copy()
    for b, a in filters:
        processed = signal.lfilter(b, a, processed, axis=0)
    outdata[:] = np.clip(processed, -1.0, 1.0)

def start_hearing_aid(gains):
    global stream, filters, running
    filters = create_filterbank(44100, gains)
    stream = sd.Stream(channels=1, samplerate=44100, callback=callback)
    stream.start()
    running = True

def stop_hearing_aid():
    global stream, running
    if stream:
        stream.stop()
        stream.close()
        stream = None
    running = False

if st.button("Start Hearing Aid") and not running:
    threading.Thread(target=start_hearing_aid, args=(gains,), daemon=True).start()
    st.success("Hearing aid mode started!")

if st.button("Stop Hearing Aid") and running:
    stop_hearing_aid()
    st.warning("Hearing aid mode stopped.")
