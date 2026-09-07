import os
import gc
from pathlib import Path
from typing import Optional, Any
from core.interfaces import AudioTranscriber


class WhisperXTranscriber(AudioTranscriber):
    """Transcriber adapter wrapping WhisperX.
    Performs transcription, phoneme alignment for word-level timestamps,
    and exports subtitles using WhisperX's built-in get_writer().
    """

    def __init__(
        self,
        model_name: str = "base",
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        batch_size: int = 16,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self.device, self.compute_type = self._resolve_device_and_compute(device, compute_type)
        self._model = None

    def _resolve_device_and_compute(
        self, device: Optional[str], compute_type: Optional[str]
    ) -> tuple[str, str]:
        try:
            import torch
            cuda_available = torch.cuda.is_available()
        except ImportError:
            cuda_available = False

        if device is None:
            resolved_device = "cuda" if cuda_available else "cpu"
        else:
            resolved_device = device

        if compute_type is None:
            # CUDA supports float16; CPU typically uses int8 or float32 in ctranslate2
            resolved_compute = "float16" if resolved_device == "cuda" else "int8"
        else:
            resolved_compute = compute_type

        return resolved_device, resolved_compute

    def _get_model(self):
        if self._model is None:
            import whisperx
            try:
                self._model = whisperx.load_model(
                    self.model_name, self.device, compute_type=self.compute_type
                )
            except Exception as e:
                # If int8 is unsupported by CPU instruction set, fallback to float32
                if self.device == "cpu" and self.compute_type != "float32":
                    print(f"[WhisperX] compute_type={self.compute_type} failed ({e}), falling back to float32...")
                    self.compute_type = "float32"
                    self._model = whisperx.load_model(
                        self.model_name, self.device, compute_type=self.compute_type
                    )
                else:
                    raise e
        return self._model

    def transcribe_and_align(self, audio_path: str, output_dir: str) -> dict[str, Any]:
        """Runs WhisperX transcription and word alignment on audio_path.
        Saves subtitles to output_dir using get_writer() and returns the native result dict.
        """
        import whisperx

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 1. Transcribe
        model = self._get_model()
        audio = whisperx.load_audio(audio_path)
        raw_result = model.transcribe(audio, batch_size=self.batch_size)

        detected_lang = raw_result.get("language", "en")

        # 2. Forced phoneme alignment for word-level timestamps
        align_model, align_metadata = whisperx.load_align_model(
            language_code=detected_lang, device=self.device
        )
        aligned_result = whisperx.align(
            raw_result["segments"],
            align_model,
            align_metadata,
            audio,
            self.device,
            return_char_alignments=False,
        )

        # Ensure language is retained in the aligned result
        if "language" not in aligned_result:
            aligned_result["language"] = detected_lang

        # Clean up alignment model from memory
        del align_model
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        # 3. Built-in WhisperX get_writer() export
        writer_args = {
            "highlight_words": False,
            "max_line_width": None,
            "max_line_count": None,
        }
        all_writer = whisperx.utils.get_writer("all", str(out_path.resolve()))
        all_writer(aligned_result, audio_path, writer_args)

        audio_stem = Path(audio_path).stem
        srt_file = out_path / f"{audio_stem}.srt"
        json_file = out_path / f"{audio_stem}.json"

        return {
            "result": aligned_result,
            "srt_path": str(srt_file.resolve()) if srt_file.exists() else None,
            "json_path": str(json_file.resolve()) if json_file.exists() else None,
        }
