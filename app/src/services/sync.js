/**
 * Fetching and submitting: the two directions of the sync.
 *
 * **Fetching** asks only for what changed since last time
 * ('?geaendert_seit='). The first time that is everything. **Submitting**
 * works through the queue in order.
 *
 * The order matters: a weighing can hang on a position that does not even
 * exist on the server yet. That is why sending is strictly from front to
 * back, and an own number is only translated into the server number when
 * sending ('serverNumber').
 *
 * The rows the server sends keep their German field names ('eingabemenge',
 * 'wiegungen', 'zuletzt_geaendert'): they belong to the API. What the app
 * adds itself ('location_id', 'server_id', the jobs) is English.
 *
 * **Every step belongs to the storage it started on** ('taken', see
 * 'storage.currentMark'). Its requests and its writes go through 'api.js',
 * which holds them back once that storage was cleared, in this window or
 * another. The writes after an answer are one guarded step each
 * ('api.guarded'). A sync started before a sign-out thus writes nothing into
 * the storage of the next account and sends nothing in its name.
 *
 * The parts live in 'sync/', one module per subject: the inventories, their
 * rows, the weighings, the review list, what waits or was refused, and the
 * queue that sends it all. This module passes on what the app uses, so the
 * app imports the sync from one place.
 */

export {
    cleanUp,
    fetchInventories,
    fetchMasterData,
    hasPendingJobs,
    inUse,
    removeInventory,
} from "./sync/inventories.js";
export { createPosition, enterQuantity, fetchPositions, removePosition } from "./sync/positions.js";
export { changeWeighing, createWeighing, removeWeighing, uuid } from "./sync/weighings.js";
export {
    enterDifference,
    enterNote,
    enterTargetCounts,
    fetchReview,
    switchLock,
    switchManualDifference,
} from "./sync/review.js";
export {
    discardRejected,
    jobKind,
    pendingJobs,
    quantityConflicts,
    rejectedJobs,
    resolveQuantityConflict,
} from "./sync/jobs.js";
export { submit } from "./sync/queue.js";
