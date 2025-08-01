import streamlit as st
import numpy as np
import torch
import soundfile as sf
import scipy.signal as signal
import matplotlib.pyplot as plt
from scipy.signal import resample_poly
import os
import tempfile
import librosa
import librosa.display
from df.enhance import enhance, init_df

st.set_page_config(page_title="Audio Processor", layout="wide")
st.title("Testing DeepFilterNet + VoiceFixer + EQ")
st.markdown("Upload a WAV file")

if "model" not in st.session_state:
    model, df_state, _ = init_df()
    st.session_state["model"] = model
    st.session_state["df_state"] = df_state

if "voicefixer" not in st.session_state:
    try:
        import voicefixer
        from voicefixer import VoiceFixer
        st.session_state["voicefixer"] = VoiceFixer()
        st.session_state["voicefixer_loaded"] = True
    except ImportError:
        try:
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "voicefixer"])
            from voicefixer import VoiceFixer
            st.session_state["voicefixer"] = VoiceFixer()
            st.session_state["voicefixer_loaded"] = True
        except Exception as e:
            st.error(f"Failed to install VoiceFixer: {str(e)}")
            st.session_state["voicefixer_loaded"] = False

if "librosa_loaded" not in st.session_state:
    try:
        import librosa
        st.session_state["librosa_loaded"] = True
    except ImportError:
        try:
            import subprocess
            import sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "librosa"])
            import librosa
            st.session_state["librosa_loaded"] = True
        except Exception as e:
            st.error(f"Failed to install librosa: {str(e)}")
            st.session_state["librosa_loaded"] = False

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

gain_display = {f"{freq} Hz": f"{g:.1f} dB" for freq, g in zip(frequencies, gains)}
st.table(gain_display)

col1, col2, col3 = st.columns(3)
deepfilter_enabled = col1.checkbox("Enable DeepFilterNet", value=True)
voicefixer_enabled = col2.checkbox("Enable VoiceFixer", value=True)
eq_enabled = col3.checkbox("Enable Equalizer", value=True)

with st.expander("Mel Spectrogram Settings"):
    n_mels = st.slider("Number of Mel bands", 64, 256, 128, 16)
    hop_length = st.slider("Hop Length", 128, 1024, 512, 64)
    n_fft = st.slider("FFT Window Size", 1024, 4096, 2048, 128)
    fmin = st.slider("Minimum Frequency", 0, 1000, 0, 10)
    fmax = st.slider("Maximum Frequency", 4000, 24000, 8000, 500)

def safe_resample(audio, orig_sr, target_sr):
    if orig_sr == target_sr:
        return audio
    gcd_val = np.gcd(orig_sr, target_sr)
    up = target_sr // gcd_val
    down = orig_sr // gcd_val
    return resample_poly(audio, up=up, down=down)

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
    a = np.array([1.0, a1/a0, a2/a0])
    return b, a

def create_filterbank(fs, gains):
    return [design_peaking_eq(fs, freq, gain) for freq, gain in zip(frequencies, gains)]

def create_mel_spectrogram(audio_data, sample_rate, title):
    fig, ax = plt.subplots(figsize=(10, 4))
    S = librosa.feature.melspectrogram(
        y=audio_data, 
        sr=sample_rate,
        n_fft=n_fft,
        hop_length=hop_length,
        n_mels=n_mels,
        fmin=fmin,
        fmax=fmax
    )
    S_dB = librosa.power_to_db(S, ref=np.max)
    img = librosa.display.specshow(
        S_dB,
        x_axis='time',
        y_axis='mel',
        sr=sample_rate,
        fmin=fmin,
        fmax=fmax,
        hop_length=hop_length,
        ax=ax
    )
    ax.set_title(title)
    fig.colorbar(img, ax=ax, format="%+2.0f dB", label='Intensity [dB]')
    return fig
