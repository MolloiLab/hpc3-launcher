------------------------------ MODULE SSHConfig ------------------------------
(***************************************************************************)
(* How HPC3 Launcher edits the user's ~/.ssh/config.                       *)
(*                                                                         *)
(* Every running VSCode session polls on its own thread, and they all edit *)
(* one shared file. Two things have gone wrong there historically:         *)
(*                                                                         *)
(*   1. a second session's write deleted the first session's block, because *)
(*      the block-matching regex was not scoped to one job id;             *)
(*   2. a session with no allocated node wrote the string Slurm gave it    *)
(*      ("None assigned") as a hostname, which makes OpenSSH reject the    *)
(*      entire file.                                                       *)
(*                                                                         *)
(* This models the read-modify-write as two separate steps, because that   *)
(* is what makes a lost update possible at all. SSHConfigNoLock.tla is the *)
(* same model with mutual exclusion removed; TLC finds a NoLostSession     *)
(* counterexample there, which is what shows these invariants have teeth.  *)
(*                                                                         *)
(* Scope, stated plainly: this verifies the DESIGN, not the Python. It is  *)
(* a hand-written abstraction and can drift from the code.                 *)
(***************************************************************************)
EXTENDS FiniteSets, Naturals

CONSTANTS
    Jobs,        \* the VSCode sessions in play
    Unassigned,  \* those Slurm reports with no node ("None assigned")
    NoJob        \* the "lock is free" placeholder

ASSUME Unassigned \subseteq Jobs
ASSUME NoJob \notin Jobs

VARIABLES
    config,     \* job ids whose block is currently in ~/.ssh/config
    committed,  \* jobs whose write completed and which nobody removed
    snapshot,   \* what each thread read, before it writes back
    pc,         \* per-thread control state
    lock        \* holder of _SSH_CONFIG_LOCK, or NoJob

vars == <<config, committed, snapshot, pc, lock>>

Init ==
    /\ config    = {}
    /\ committed = {}
    /\ snapshot  = [j \in Jobs |-> {}]
    /\ pc        = [j \in Jobs |-> "idle"]
    /\ lock      = NoJob

(* The guard added by clean_node_name: a session with no real node never *)
(* reaches the file at all.                                              *)
RefuseUnassigned(j) ==
    /\ pc[j] = "idle"
    /\ j \in Unassigned
    /\ pc' = [pc EXCEPT ![j] = "done"]
    /\ UNCHANGED <<config, committed, snapshot, lock>>

Acquire(j) ==
    /\ pc[j] = "idle"
    /\ j \notin Unassigned
    /\ lock = NoJob
    /\ lock' = j
    /\ pc' = [pc EXCEPT ![j] = "read"]
    /\ UNCHANGED <<config, committed, snapshot>>

Read(j) ==
    /\ pc[j] = "read"
    /\ snapshot' = [snapshot EXCEPT ![j] = config]
    /\ pc' = [pc EXCEPT ![j] = "write"]
    /\ UNCHANGED <<config, committed, lock>>

(* Scoped to this job: everything the thread read is preserved, and only  *)
(* this job's own block is (re)written. The historical bug was equivalent *)
(* to  config' = {j}  -- it dropped every other session's block.          *)
Write(j) ==
    /\ pc[j] = "write"
    /\ config'    = snapshot[j] \cup {j}
    /\ committed' = committed \cup {j}
    /\ lock'      = NoJob
    /\ pc'        = [pc EXCEPT ![j] = "done"]
    /\ UNCHANGED snapshot

Step(j) == RefuseUnassigned(j) \/ Acquire(j) \/ Read(j) \/ Write(j)

Next == \E j \in Jobs : Step(j)

Spec == Init /\ [][Next]_vars /\ \A j \in Jobs : WF_vars(Step(j))

-----------------------------------------------------------------------------

TypeOK ==
    /\ config    \subseteq Jobs
    /\ committed \subseteq Jobs
    /\ lock \in Jobs \cup {NoJob}
    /\ pc \in [Jobs -> {"idle", "read", "write", "done"}]
    /\ snapshot \in [Jobs -> SUBSET Jobs]

(* No session's block is ever lost by another session's write. *)
NoLostSession == committed \subseteq config

(* Nothing without a real node ever reaches the file -- the invariant whose *)
(* violation made OpenSSH throw out the user's whole config.                *)
NeverWritesUnassigned == config \cap Unassigned = {}

(* _SSH_CONFIG_LOCK: at most one thread inside the read-modify-write. *)
MutualExclusion ==
    Cardinality({j \in Jobs : pc[j] \in {"read", "write"}}) <= 1

(* Every thread finishes: the lock is always released, so nobody starves. *)
Termination == <>(\A j \in Jobs : pc[j] = "done")

=============================================================================
