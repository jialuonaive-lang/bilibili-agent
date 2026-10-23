"""Optional isolated CPU transcription worker, using a preinstalled Whisper small model."""
import os,platform,sys
from pathlib import Path
os.environ.setdefault('HF_HUB_OFFLINE','1')
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('MKL_NUM_THREADS','2')
os.environ.setdefault('CT2_PACKED_GEMM','0')
if sys.platform=='win32':
    platform.system=lambda:'Windows'
    platform.release=lambda:'11' if sys.getwindowsversion().build>=22000 else '10'
from faster_whisper import WhisperModel
model=WhisperModel(os.environ.get('BILI_ASR_MODEL','small'),device='cpu',compute_type='int8',cpu_threads=2,num_workers=1)
segments,_=model.transcribe(sys.argv[1],language='zh',beam_size=3,vad_filter=True)
text='\n'.join(s.text.strip() for s in segments if s.text.strip())
target=Path(sys.argv[2]);temp=target.with_suffix('.tmp');temp.write_text(text,encoding='utf-8');temp.replace(target)
