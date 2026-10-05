# ER-4U link meshes (not in the repository)

The 3D view (`python -m scorbot.arm_view`) draws real link shapes if it finds
six STL files here. Without them it draws the arm as a line, and everything
else works the same.

The files are a community SolidWorks model of the ER-4U. They are **not
committed**: their origin states no licence, so `.gitignore` keeps everything
in this folder except this README out of git. Do not force-add them.

| File | Link it draws |
|---|---|
| `base_Link.STL` | base |
| `shoulder_Link.STL` | turret (the part that turns on the base) |
| `elbow_Link.STL` | upper arm |
| `pitch_Link.STL` | forearm |
| `roll_Link.STL` | wrist |
| `gripper_Link.STL` | gripper body |

The file names are the model's own and do not match the links they draw.

Where they come from:

- Original: <https://github.com/baijuch/sboter4u> (`MoveIt! Config Packages/sboter4u_model/meshes`), 2015, no licence stated.
- The same files in a GPL-3.0 repository: <https://github.com/greenpro/sac_description> (`meshes/`).
- How each mesh sits on its link (the fixed offsets in `scorbot/arm_view.py`): derived by <https://github.com/talos-rit/scorbot_ros2> for the same files.

To fetch them (GitHub CLI), from the repository root:

```powershell
foreach ($f in 'base_Link','shoulder_Link','elbow_Link','pitch_Link','roll_Link','gripper_Link') {
  gh api "repos/greenpro/sac_description/contents/meshes/$f.STL" -H "Accept: application/vnd.github.raw" > "models\er4u_meshes\$f.STL"
}
```

Or keep them elsewhere and set `SCORBOT_MESH_DIR` to that folder.

The model is a drawing aid. It was not measured against our arm, and the
picture it makes is labelled unvalidated.
