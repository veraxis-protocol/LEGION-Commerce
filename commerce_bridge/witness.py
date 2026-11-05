import queue
import subprocess
import threading
import time
from commerce_adapter.contracts import Denied,require


class LeanWitness:
    """Bounded, persistent execution of the upstream compiled verdict. No cache."""
    def __init__(self,binary,timeout=2):
        self.timeout=timeout
        self.lock=threading.Lock()
        try:
            self.process=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,text=True,bufsize=1,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except OSError:
            raise Denied("LEAN_UNAVAILABLE") from None
        self.answers=queue.Queue()
        def read():
            try:
                for line in self.process.stdout: self.answers.put(line.strip())
            finally: self.answers.put(None)
        self.reader=threading.Thread(target=read,daemon=True)
        self.reader.start()

    def evaluate(self,f,values):
        require(type(f) is int and 0<=f<=1000 and len(values)<=10000
                and all(v in ("T","F","-") for v in values),"LEAN_INPUT_SCHEMA")
        with self.lock:
            require(self.process.poll() is None,"LEAN_UNAVAILABLE")
            expired=threading.Event()
            finished=threading.Event()
            def stop_blocked_io():
                if not finished.is_set():
                    expired.set()
                    if self.process.poll() is None: self.process.kill()
            # Include a blocked stdin write in the deadline, not only stdout.
            timer=threading.Timer(self.timeout,stop_blocked_io)
            timer.daemon=True
            timer.start()
            try:
                self.process.stdin.write(f"{f} {''.join(values)}\n")
                self.process.stdin.flush()
                answer=self.answers.get(timeout=self.timeout)
                if expired.is_set():
                    self.close()
                    raise Denied("LEAN_TIMEOUT")
                require(answer in ("T","F","-"),"LEAN_INVALID_RESPONSE")
                return answer
            except queue.Empty:
                self.close()
                raise Denied("LEAN_TIMEOUT") from None
            except (OSError,ValueError):
                self.close()
                raise Denied("LEAN_TIMEOUT" if expired.is_set() else "LEAN_UNAVAILABLE") from None
            finally:
                finished.set()
                timer.cancel()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        if threading.current_thread() is not self.reader: self.reader.join(timeout=3)
        for stream in (self.process.stdin,self.process.stdout):
            if stream: stream.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
