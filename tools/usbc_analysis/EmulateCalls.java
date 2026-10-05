// Run single functions of the analysed program in Ghidra's p-code emulator and
// write back the memory they produce. The program is not loaded by Windows and
// no Windows or driver code runs: memory, registers and arguments are set by
// hand from a spec file, and only leaf arithmetic functions are meant for this.
//
// Arguments: SPEC_FILE OUT_FILE. Spec format, one directive per line:
//   case NAME                 start a case (state from earlier cases is kept)
//   mem ADDRESS HEXBYTES      write bytes
//   reg NAME VALUE            write a register (hex or decimal)
//   arg VALUE                 a 32-bit stack argument, in call order
//   call ADDRESS              run from ADDRESS until it returns
//   read ADDRESS LENGTH       append LENGTH bytes at ADDRESS to the case output
//   end                       finish the case; OUT_FILE gets "NAME HEX HEX ..."
import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import ghidra.app.emulator.EmulatorHelper;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;

public class EmulateCalls extends GhidraScript {
    private static final long STACK_TOP = 0x00300000L;
    private static final long RETURN_SENTINEL = 0x00000F00L;
    private static final int MAX_STEPS = 2_000_000;

    private Address at(long value) {
        return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(value);
    }

    private static byte[] unhex(String text) {
        byte[] out = new byte[text.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(text.substring(2 * i, 2 * i + 2), 16);
        }
        return out;
    }

    private static String hex(byte[] data) {
        StringBuilder sb = new StringBuilder();
        for (byte b : data) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static byte[] le32(long value) {
        return new byte[] {(byte) value, (byte) (value >> 8), (byte) (value >> 16),
                           (byte) (value >> 24)};
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        EmulatorHelper emu = new EmulatorHelper(currentProgram);
        int cases = 0;
        try (BufferedReader in = new BufferedReader(new FileReader(new File(args[0])));
             PrintWriter out = new PrintWriter(new File(args[1]), "UTF-8")) {
            String name = null;
            List<Long> callArgs = new ArrayList<>();
            StringBuilder result = new StringBuilder();
            String line;
            while ((line = in.readLine()) != null) {
                String[] t = line.trim().split("\\s+");
                if (t.length == 0 || t[0].isEmpty() || t[0].startsWith("#")) continue;
                switch (t[0]) {
                    case "case":
                        name = t[1];
                        callArgs.clear();
                        result.setLength(0);
                        break;
                    case "mem":
                        emu.writeMemory(at(Long.decode(t[1])), unhex(t[2]));
                        break;
                    case "reg":
                        emu.writeRegister(t[1], Long.decode(t[2]));
                        break;
                    case "arg":
                        callArgs.add(Long.decode(t[1]));
                        break;
                    case "call": {
                        long sp = STACK_TOP - 4L * (callArgs.size() + 1);
                        emu.writeMemory(at(sp), le32(RETURN_SENTINEL));
                        for (int i = 0; i < callArgs.size(); i++) {
                            emu.writeMemory(at(sp + 4L * (i + 1)), le32(callArgs.get(i)));
                        }
                        emu.writeRegister("ESP", sp);
                        emu.writeRegister(emu.getPCRegister(), Long.decode(t[1]));
                        int steps = 0;
                        while (emu.getExecutionAddress().getOffset() != RETURN_SENTINEL) {
                            if (!emu.step(monitor)) {
                                throw new RuntimeException("case " + name + ": emulation stopped at "
                                    + emu.getExecutionAddress() + ": " + emu.getLastError());
                            }
                            if (++steps > MAX_STEPS) {
                                throw new RuntimeException("case " + name + ": no return after "
                                    + MAX_STEPS + " steps");
                            }
                        }
                        break;
                    }
                    case "read":
                        result.append(' ').append(hex(
                            emu.readMemory(at(Long.decode(t[1])), Integer.decode(t[2]))));
                        break;
                    case "end":
                        out.println(name + result);
                        cases++;
                        break;
                    default:
                        throw new RuntimeException("unknown directive: " + line);
                }
            }
        } finally {
            emu.dispose();
        }
        println("EmulateCalls: " + cases + " cases -> " + args[1]);
    }
}
