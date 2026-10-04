// Dump the decompiled C of every function in the program to one file.
import java.io.File;
import java.io.PrintWriter;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

public class ExportDecomp extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File out = new File(args[0]);
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        int ok = 0, bad = 0;
        try (PrintWriter w = new PrintWriter(out, "UTF-8")) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                if (f.isExternal() || f.isThunk()) continue;
                DecompileResults r = decomp.decompileFunction(f, 60, monitor);
                w.println("// ===== " + f.getEntryPoint() + " " + f.getName(true));
                if (r != null && r.decompileCompleted()) {
                    w.println(r.getDecompiledFunction().getC());
                    ok++;
                } else {
                    w.println("// decompile failed");
                    bad++;
                }
            }
        }
        println("ExportDecomp: " + ok + " ok, " + bad + " failed -> " + out);
    }
}
