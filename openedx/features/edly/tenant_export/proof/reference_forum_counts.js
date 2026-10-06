// Reference forum counts for the MIT export proof run (EDLYPRODUCT-8584 Phase 3),
// independent of tenant_export/forum.py (written from EDM's migrate_forum.py logic).
// Read-only. Run on the Koa demo site's forum Mongo, compare with
//   export_tenant_forum MIT --scope scope.json --out-dir ... --dry-run
//
//   mongosh "<forum uri>/cs_comments_service" --quiet reference_forum_counts.js
// (legacy shell: `mongo <uri>/cs_comments_service --quiet reference_forum_counts.js`)
//
// EDIT: orgs = scope.json "course_orgs" (real-case orgs; the match below is case-insensitive).
var orgs = ["MITx"];   // <-- set from scope.json

var esc = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); };
var re = new RegExp("^course-v1:(" + orgs.map(esc).join("|") + ")\\+", "i");
var flt = { course_id: re };

print("global contents (wrong-DB guard, must be > 0): " + db.contents.countDocuments({}));
print("contents:  " + db.contents.countDocuments(flt));
print("  threads:  " + db.contents.countDocuments({ course_id: re, _type: "CommentThread" }));
print("  comments: " + db.contents.countDocuments({ course_id: re, _type: "Comment" }));
print("  cohorted threads (group_id set): " +
      db.contents.countDocuments({ course_id: re, _type: "CommentThread", group_id: { $ne: null } }));

var refs = {};
var add = function (v) { if (v) { refs[String(v)] = 1; } };
var threadIds = [];
db.contents.find(flt).forEach(function (d) {
  if (d._type === "CommentThread") { threadIds.push(String(d._id)); }
  add(d.author_id); add(d.closed_by_id);
  (d.abuse_flaggers || []).forEach(add);
  (d.historical_abuse_flaggers || []).forEach(add);
  if (d.votes) { (d.votes.up || []).forEach(add); (d.votes.down || []).forEach(add); }
  if (d.endorsement) { add(d.endorsement.user_id); }
  (d.edit_history || []).forEach(function (e) { add(e && e.author_id); });
});
var refIds = Object.keys(refs);
print("referenced user ids: " + refIds.length);
print("  with a users doc (= users.jsonl rows): " + db.users.countDocuments({ _id: { $in: refIds } }));

// source_id is the thread _id as a HEX STRING (not ObjectId) -- hence String(d._id) above.
print("thread-follow subscriptions (= subscriptions.jsonl rows): " +
      db.subscriptions.countDocuments({ source_id: { $in: threadIds }, source_type: "CommentThread" }));
print("user-follow subscriptions skipped: " +
      db.subscriptions.countDocuments({ source_id: { $in: threadIds }, source_type: { $ne: "CommentThread" } }));
