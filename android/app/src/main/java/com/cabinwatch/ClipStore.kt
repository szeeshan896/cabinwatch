package com.cabinwatch

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The recordings, held on this phone.
 *
 * Footage never goes to the server on its own. One file per minute lands in
 * the app's own folder, the oldest are recycled, and only a clip you actually
 * open from the dashboard is sent anywhere. So a power cut or an internet
 * outage does not cost you the evidence.
 *
 * The one subtlety is the keep-list. When movement or a sound fires an alert,
 * the minute it happened in is marked, and marked clips get a far longer
 * retention than ordinary ones and are the last thing deleted when space runs
 * short. The boring hours recycle; the moment somebody walked in survives.
 */
class ClipStore(context: Context, private val settings: Settings) {

    private val dir: File = File(context.filesDir, "clips").apply { mkdirs() }
    private val keepFile = File(dir, "keep.json")
    private val keep = HashSet<String>()

    init {
        loadKeep()
    }

    val directory: File get() = dir

    fun newClipFile(): File {
        val stamp = SimpleDateFormat("yyyy-MM-dd_HH-mm-ss", Locale.US).format(Date())
        return File(dir, "$stamp.mp4")
    }

    // ---- the keep-list -----------------------------------------------------
    private fun loadKeep() {
        try {
            if (!keepFile.exists()) return
            val arr = JSONArray(keepFile.readText())
            for (i in 0 until arr.length()) keep.add(arr.getString(i))
        } catch (e: Exception) {
            Log.w(TAG, "could not read keep.json: ${e.message}")
        }
    }

    private fun saveKeep() {
        try {
            keepFile.writeText(JSONArray(keep.toList()).toString())
        } catch (e: Exception) {
            Log.w(TAG, "could not write keep.json: ${e.message}")
        }
    }

    /** Mark a file as worth keeping, because something happened during it. */
    fun markKeep(name: String?) {
        if (name.isNullOrEmpty()) return
        if (keep.add(name)) saveKeep()
    }

    fun isKept(name: String) = keep.contains(name)

    // ---- listing -----------------------------------------------------------
    fun list(limit: Int = 400, excluding: String? = null): JSONArray {
        val out = JSONArray()
        val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".mp4") }
            ?.sortedByDescending { it.lastModified() } ?: return out
        val pretty = SimpleDateFormat("dd MMM yyyy, HH:mm:ss", Locale.getDefault())
        for (f in files.take(limit)) {
            // Skip the file still being written - it will not play yet.
            if (f.name == excluding) continue
            out.put(JSONObject().apply {
                put("file", f.name)
                put("size_mb", Math.round(f.length() / 104857.6) / 10.0)
                put("at", f.lastModified() / 1000)
                put("when", pretty.format(Date(f.lastModified())))
                put("keep", keep.contains(f.name))
            })
        }
        return out
    }

    fun count(): Int =
        dir.listFiles { f -> f.name.endsWith(".mp4") }?.size ?: 0

    fun usedBytes(): Long =
        dir.listFiles { f -> f.name.endsWith(".mp4") }?.sumOf { it.length() } ?: 0L

    fun freeGb(): Double {
        val budget = settings.maxStorageMb.toLong() * 1024L * 1024L
        val left = (budget - usedBytes()).coerceAtLeast(0L)
        return Math.round(left / 1073741824.0 * 10.0) / 10.0
    }

    /** Resolve a name to a file, refusing anything that escapes our folder. */
    fun resolve(name: String?): File? {
        if (name.isNullOrEmpty()) return null
        if (name.contains('/') || name.contains('\\') || name.contains("..")) return null
        val f = File(dir, name)
        // Refuse anything that resolves outside our own folder.
        if (!f.canonicalPath.startsWith(dir.canonicalPath + File.separator)) return null
        return if (f.isFile) f else null
    }

    // ---- retention ---------------------------------------------------------
    /**
     * Age files out and stay inside the storage budget. Ordinary clips go
     * first; event clips are only touched when there is nothing else to give.
     */
    fun sweep(currentlyWriting: String?) {
        try {
            val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".mp4") }
                ?.filter { it.name != currentlyWriting } ?: return

            val now = System.currentTimeMillis()
            val ordinaryMs = settings.retentionHours.toLong() * 3600_000L
            val eventMs = ordinaryMs * 4
            var total = 0L
            val survivors = ArrayList<File>()

            for (f in files) {
                val kept = keep.contains(f.name)
                val limit = if (kept) eventMs else ordinaryMs
                if (now - f.lastModified() > limit) {
                    delete(f)
                } else {
                    total += f.length()
                    survivors.add(f)
                }
            }

            val budget = settings.maxStorageMb.toLong() * 1024L * 1024L
            if (total > budget) {
                // Ordinary clips first, oldest first; event clips last.
                survivors.sortWith(
                    compareBy({ if (keep.contains(it.name)) 1 else 0 },
                              { it.lastModified() })
                )
                for (f in survivors) {
                    if (total <= budget) break
                    total -= f.length()
                    delete(f)
                }
            }

            // Drop names from the keep-list once their file is gone.
            val alive = dir.list()?.toSet() ?: emptySet()
            val gone = keep.filter { it !in alive }
            if (gone.isNotEmpty()) {
                keep.removeAll(gone.toSet())
                saveKeep()
            }
        } catch (e: Exception) {
            Log.w(TAG, "sweep failed: ${e.message}")
        }
    }

    private fun delete(f: File) {
        if (f.delete()) keep.remove(f.name)
    }

    companion object {
        private const val TAG = "ClipStore"
    }
}
