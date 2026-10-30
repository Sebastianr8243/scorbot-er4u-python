// Logs every WriteFile/ReadFile on a handle the vendor DLL opened with CreateFileA on its DriverLink
// (the named pipe of the desk fake, or the ERUSBDevice0 device on the real arm). One JSON line per call.
// Read-only: it observes the calls and changes nothing.
// Attach BEFORE the DLL opens the device: only handles opened after the attach are logged.
// Create the output folder first (OUT_PATH below).
const OUT_PATH = "C:\\usbc_capture\\frida_io.jsonl";
Process.setExceptionHandler(e => { send({exc: e.type, addr: String(e.address), ctx: String(e.context.pc)}); return false; });
const out = new File(OUT_PATH, "w");
send({loaded: true, arch: Process.arch, k32: String(Process.getModuleByName("kernel32.dll").base)});
const t0 = Date.now();
const links = new Set();
let nW = 0, nR = 0, n = 0;

function hex(ptr, len) {
  const b = new Uint8Array(ptr.readByteArray(len));
  return Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
}
function log(o) { o.t = (Date.now() - t0) / 1000; out.write(JSON.stringify(o) + "\n"); if (++n % 20 === 0) out.flush(); }

const k32 = Process.getModuleByName("kernel32.dll");
Interceptor.attach(k32.getExportByName("CreateFileA"), {
  onEnter(a) { this.path = a[0].readCString(); },
  onLeave(r) {
    if (this.path && /scorbot_fake|ERUSBDevice|INTDevice/i.test(this.path)) {
      links.add(r.toString()); log({ ev: "open", path: this.path, handle: r.toString() });
    }
  }
});
Interceptor.attach(k32.getExportByName("WriteFile"), {
  onEnter(a) {
    this.h = a[0].toString(); this.len = a[2].toInt32(); this.buf = a[1]; this.pw = a[3];
    this.on = links.has(this.h);
    if (this.on) this.data = hex(this.buf, this.len);
  },
  onLeave(r) {
    if (!this.on) return;
    nW++; log({ ev: "W", len: this.len, ok: r.toInt32(), done: this.pw.isNull() ? null : this.pw.readU32(), hex: this.data });
  }
});
Interceptor.attach(k32.getExportByName("ReadFile"), {
  onEnter(a) { this.h = a[0].toString(); this.buf = a[1]; this.len = a[2].toInt32(); this.pr = a[3]; this.on = links.has(this.h); },
  onLeave(r) {
    if (!this.on) return;
    const got = this.pr.isNull() ? 0 : this.pr.readU32();
    nR++; log({ ev: "R", req: this.len, ok: r.toInt32(), got: got, hex: got ? hex(this.buf, got) : "" });
  }
});
rpc.exports = { stats() { out.flush(); return { writes: nW, reads: nR, links: Array.from(links) }; } };
setInterval(() => { out.flush(); send({ writes: nW, reads: nR }); }, 2000);
