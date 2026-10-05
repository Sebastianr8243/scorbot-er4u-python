// Dump the disassembly listing of every function in the program to one file.
// The decompiler hides x87 floating-point arithmetic behind calls like __ftol,
// so formulas have to be read from the instructions.
import java.io.File;
import java.io.PrintWriter;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;

public class ExportAsm extends GhidraScript {
    @Override
    public void run() throws Exception {
        File out = new File(getScriptArgs()[0]);
        int functions = 0;
        try (PrintWriter w = new PrintWriter(out, "UTF-8")) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                if (f.isExternal() || f.isThunk()) continue;
                // Same marker as ExportDecomp.java, so query.py reads both dumps.
                w.println("// ===== " + f.getEntryPoint() + " " + f.getName(true));
                InstructionIterator it = currentProgram.getListing().getInstructions(f.getBody(), true);
                while (it.hasNext()) {
                    Instruction i = it.next();
                    w.println(i.getAddress() + "  " + i);
                }
                w.println();
                functions++;
            }
        }
        println("ExportAsm: " + functions + " functions -> " + out);
    }
}
