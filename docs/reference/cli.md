# Referencia de la CLI

Generada por `scripts/docs/generate.py` recorriendo `agentic.cli.build_parser()`. Cada sección es la ayuda de un comando, tal como la imprime `agentic`. No se edita a mano.

## `agentic`

```text
usage: agentic [-h]
               {run,resume,close,merge,land,restore,stop,eval,stack,scores,dashboards,poll,reflect,perf} ...

positional arguments:
  {run,resume,close,merge,land,restore,stop,eval,stack,scores,dashboards,poll,reflect,perf}
    run                 run one task through the feature path
    resume              resume the task of issue N where it paused
    close               record the human's verdict on a task's pull request and close it
    merge               merge a task's approved pull request when the checks pass on its head merged
                        with the upstream master (GH-178, ADR-0045); the poller spawns it, nobody
                        else needs to
    land                take the landing step for a task's approved pull request that the automatic
                        merge refused (GH-239, ADR-0048); the poller spawns it, nobody else needs to
    restore             bring an archived, approved task back from its archive/run<N>-<sha> refs --
                        branches, document and pull request -- so it can land without a fresh run
                        (GH-406)
    stop                stop the run of issue N on purpose and mark it so the poller never retakes
                        it (GH-466); `agentic resume --issue N` continues it
    eval                drive or score README.md 9's fixed five-task evaluation set
    stack               the product's own stack (ADR-0034)
    scores              a task's numbers as Langfuse scores (GH-273)
    dashboards          the Langfuse dashboards as code (GH-274)
    poll                a long-lived process per product (GH-137): closes merged pull requests,
                        retakes a pause an external API cut caused once its own wait elapses, and
                        launches the next unblocked issue; role runs never pass
                        AGENTIC_MAX_CONCURRENT_RUNS, plus one reserved slot that only a land step
                        for a delivered task may use (`rules.LAND_HEADROOM`, ADR-0045), while a
                        merge or checks-only land child runs beside them, at most one per machine
    reflect             what closed tasks' returns teach each role (ADR-0043): candidate playbook
                        deltas on the branch agentic/playbook, in one pull request for a person to
                        merge
    perf                measure a product against the performance budgets its stack declares
                        (GH-318, ADR-0059); exits 0 within, 1 over, 2 inconclusive, 3 refused

options:
  -h, --help            show this help message and exit
```

## `agentic run`

```text
usage: agentic run [-h] [--issue N] [--issue-input FILE] [--fresh] [--arm NAME]
                   [--experiment-file PATH] [--product-dir PRODUCT_DIR] [--verbose] [--detach]
                   [title] [description]

positional arguments:
  title
  description

options:
  -h, --help            show this help message and exit
  --issue N             take title and description from GitHub issue N of the product repo (needs
                        gh)
  --issue-input FILE    take the task from this frozen entry file instead of a GitHub issue (an
                        evaluation run's input; `eval run` writes it)
  --fresh               start over a task that is paused or ran before: its checkpoint is dropped
                        and its previous branches, if any, archived under archive/run<N>-<sha>/
                        before a fresh run begins
  --arm NAME            run the task as this arm of the product's experiment: its switches are
                        written to the task's document and applied to every role (refused when the
                        environment sets a switch the experiment's arms name)
  --experiment-file PATH
                        the experiment declaration to read the arm from (default:
                        <product>/.agentic/experiment.yaml)
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --verbose             stream each role's generation to stderr
  --detach              detach from this session (setsid), print the pid and the log path, and exit
```

## `agentic resume`

```text
usage: agentic resume [-h] --issue N [--task-budget USD] [--estimate USD] [--from-document]
                      [--dry-run] [--yes] [--by-poller] [--product-dir PRODUCT_DIR] [--verbose]
                      [--detach]

options:
  -h, --help            show this help message and exit
  --issue N
  --task-budget USD     raise the task cap for this run, never lower it (AGENTIC_TASK_BUDGET_USD)
  --estimate USD        the cost estimate to put in force when the task paused at the cap of its
                        estimate (default: the one the task proposed); an error on any other pause.
                        --task-budget governs only the flat task cap
  --from-document       seed the state from the task document instead of the checkpoint
  --dry-run             show what would resume and stop, spending nothing
  --yes                 do not ask for confirmation
  --by-poller           the poller spawned this resume (GH-466): it does not reset the retake budget
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --verbose             stream each role's generation to stderr
  --detach              detach from this session (setsid), print the pid and the log path, and exit
```

## `agentic close`

```text
usage: agentic close [-h] [--issue N] [--all] [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --issue N
  --all                 close every task document with a pull request and no recorded closure (Tech
                        Lead plan, subtask `close-all`)
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic merge`

```text
usage: agentic merge [-h] --issue N [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --issue N
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic land`

```text
usage: agentic land [-h] --issue N [--product-dir PRODUCT_DIR] [--run-kind {land_checks,land_model}]

options:
  -h, --help            show this help message and exit
  --issue N
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --run-kind {land_checks,land_model}
                        the kind this child registers under, set by the poller at spawn: land_checks
                        for a step that runs no model, land_model (the default) for any other
```

## `agentic restore`

```text
usage: agentic restore [-h] --issue N --from archive/run<N>-<sha> [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --issue N
  --from archive/run<N>-<sha>
                        the prefix the archived run's branches live under
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic stop`

```text
usage: agentic stop [-h] --issue N [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --issue N
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic eval`

```text
usage: agentic eval [-h] {run,score,compare,findings,shadow} ...

positional arguments:
  {run,score,compare,findings,shadow}
    run                 run every entry of the evaluation set from its pinned base, once per arm,
                        each in its own root and one at a time
    score               write one JSONL line per evaluation-set task, read off its own document
    compare             ADR-0028's scorecard for two baselines, from task documents and local git
                        alone
    findings            where important findings and returns come from, over the task documents
                        closed in a window (GH-354); reads documents only
    shadow              what the single review missed that its shadow found, over the task documents
                        with shadow reviews (GH-473); reads documents only

options:
  -h, --help            show this help message and exit
```

## `agentic eval run`

```text
usage: agentic eval run [-h] [--experiment ID] --budget USD [--yes] [--dry-run] [--detach]
                        [--set PATH] [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --experiment ID       run each entry once per arm of this experiment (master's
                        .agentic/experiment.yaml); without it, once as `default`
  --budget USD          stop launching further tasks once the observed spend would exceed this
  --yes                 confirm the estimated spend; required to launch anything
  --dry-run             show the set, the resolved issues and the estimate, spending nothing
  --detach              detach this eval driver (setsid), print the pid and the log path, and exit
  --set PATH            override the product's own .agentic/eval-set.yaml
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic eval score`

```text
usage: agentic eval score [-h] --out PATH [--set PATH] [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --out PATH            destination JSONL; refuses if it exists
  --set PATH            override the product's own .agentic/eval-set.yaml
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic eval compare`

```text
usage: agentic eval compare [-h] [--experiment ID] [--product-dir PRODUCT_DIR] [--system-repo PATH]
                            [from] [to]

positional arguments:
  from                  the baseline compared from
  to                    the baseline compared against `from`

options:
  -h, --help            show this help message and exit
  --experiment ID       compare the arms of one experiment against each other (instead of two
                        baselines)
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --system-repo PATH    the system checkout to resolve a document's own system_commit against
                        (defaults to the pinned stage0 checkout)
```

## `agentic eval findings`

```text
usage: agentic eval findings [-h] --since DATE --until DATE [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --since DATE          first day, ISO
  --until DATE          last day, ISO
  --product-dir, --product PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic eval shadow`

```text
usage: agentic eval shadow [-h] [--product-dir PRODUCT_DIR]

options:
  -h, --help            show this help message and exit
  --product-dir, --product PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
```

## `agentic stack`

```text
usage: agentic stack [-h] {review} ...

positional arguments:
  {review}
    review    the Tech Lead in proactive mode decides whether the product's stack still fits its
              Brief; never a direct change, always a proposal issue

options:
  -h, --help  show this help message and exit
```

## `agentic stack review`

```text
usage: agentic stack review [-h] [--product-dir PRODUCT_DIR] [--reason TEXT] [--dry-run]

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --reason TEXT         a reason of your own; counts as a trigger on its own and is reproduced
                        verbatim among the signals in the issue body
  --dry-run             show which signals hold, the anchor and what would be opened; spends
                        nothing, takes no lock, writes no file and makes no `gh` call
```

## `agentic scores`

```text
usage: agentic scores [-h] {backfill} ...

positional arguments:
  {backfill}
    backfill  write the scores of every finished task document of the product, once; a rerun sends
              the same ids, so it fills gaps and never duplicates

options:
  -h, --help  show this help message and exit
```

## `agentic scores backfill`

```text
usage: agentic scores backfill [-h] [--product-dir PRODUCT_DIR] [--dry-run]

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --dry-run             read every document, pull request and issue and print what would be written;
                        writes nothing to Langfuse
```

## `agentic dashboards`

```text
usage: agentic dashboards [-h] {apply} ...

positional arguments:
  {apply}
    apply     write the product's four dashboards into the local Langfuse's Postgres, idempotently
              (ADR-0051)

options:
  -h, --help  show this help message and exit
```

## `agentic dashboards apply`

```text
usage: agentic dashboards apply [-h] [--product-dir PRODUCT_DIR] [--dry-run] [--verify]

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --dry-run             print what would be created, updated and deleted; writes nothing
  --verify              also count the rows each widget reads in ClickHouse; a widget that reads
                        none fails unless its score is omitted by design
```

## `agentic poll`

```text
usage: agentic poll [-h] [--product-dir PRODUCT_DIR] [--detach] [--interval SECONDS] [--dry-run]
                    [--once]

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --detach              detach this poller (setsid), print the pid and the log path, and exit
  --interval SECONDS    how long to wait between rounds (default 60)
  --dry-run             with --once: one round from reads only, printing what it would do and
                        writing nothing; the upstream fetch is the one exception. The five sweeps
                        are skipped, so a blocker the live close sweep would close does not release
                        its dependent (GH-348), and the first-seen exempt stamp is not recorded;
                        exits 1 if a call was unclassified
  --once                run one round and exit (only with --dry-run)
```

## `agentic reflect`

```text
usage: agentic reflect [-h] [--product-dir PRODUCT_DIR] (--issue N | --all) [--since YYYY-MM-DD]

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --issue N             reflect on issue N's task
  --all                 reflect on every eligible closed task, up to the cap
  --since YYYY-MM-DD    reach back to tasks closed on or after this day (the first run reflects
                        nothing older than itself otherwise)
```

## `agentic perf`

```text
usage: agentic perf [-h] [--product-dir PRODUCT_DIR] [--revision SHA] [--overlay PATH]
                    product [scenario]

positional arguments:
  product               the product's name, as its stack declares it
  scenario              one scenario; default every one

options:
  -h, --help            show this help message and exit
  --product-dir PRODUCT_DIR
                        root of the product repo (AGENTIC_PRODUCT_DIR)
  --revision SHA        the code under test (default HEAD); the harness is HEAD
  --overlay PATH        copy PATH from the product's HEAD into the measured checkout (repeatable)
```
