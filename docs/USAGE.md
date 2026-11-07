# Usage and Oracle Always Free budgets

The administrator-only **Usage** tab combines local server measurements,
observed monthly totals, editable budgets and optional Oracle Console totals.
It does not request an Oracle API key or query billing.

## What updates automatically

The account manager takes a sample about once a minute and publishes a bounded,
read-only summary for the web service:

- CPU utilization across the VM, using changes in Linux CPU counters.
- Total/used/available memory, swap and data-filesystem disk space. A distinct
  root filesystem gets its own reading.
- Average inbound/outbound rates from external interfaces. Loopback, Docker
  bridges and veth links are excluded to avoid counting container traffic twice.
- Local file sizes for each existing dashboard account, including media,
  archives, device state and logs. File contents and filenames are not published.
  A scan is limited to 50,000 entries / about half a second and reports partial work.
- Observed outbound traffic and allocated A1 OCPU-hours/GB-hours in the UTC month.
  Enter the allocated OCPUs and memory from Oracle Console first; CPU load and
  Linux's available RAM are not the provisioned/billable allocation.

The first sample establishes a baseline. CPU/rate estimates need a second sample.
Small non-zero compute amounts display as `<0.1` rather than zero; transfer uses
readable binary units for the observed bytes.

## Allocation and budgets

Open **Usage → Allocation and budgets**, enter the A1 resources provisioned in
Oracle Console, check the budgets and save. Both OCPUs and allocated memory must
be provided together, or both left blank.

The current reference was checked against Oracle's official documentation on
**7 November 2026**:

| Resource | Default dashboard reference |
| --- | --- |
| Ampere A1 compute | 1,500 OCPU-hours/month |
| Ampere A1 allocated memory | 9,000 GB-hours/month |
| Outbound transfer | 10 TB/month |
| Combined boot + block volumes | 200 GB provisioned |
| Combined boot/block volume backups | 5 |
| Combined object/archive storage | 20 GB for Always Free-only accounts |
| Object Storage API requests | 50,000/month |

These budgets are editable. Confirm eligibility, home-region requirements and
the actual allowances for your tenancy in Oracle Console. Trial/paid account
object-storage allowances differ from the combined Always Free-only reference.
Older guides may describe larger A1 allowances.

A full-month allocation estimate is the saved VM size multiplied by the number
of hours in that UTC calendar month. It is an estimate for this VM, not a
prediction of the entire account's charge. Near/reached-budget states appear
at 85%/100%; an allocation forecast above the budget is highlighted separately.

Transfer budgets deliberately use decimal TB (1 TB = 10^12 bytes); OS memory,
filesystem and observed transfer displays use binary units (GiB/MiB). Provider
metering and billing units can differ; the console is authoritative.

## Observation scope and restarts

Monthly totals begin when monitoring is enabled, not retroactively at the start
of the month. Earlier usage and other VMs/services are excluded. Unknown values
are shown as unavailable, not zero. Partial coverage is labelled with its start
time; a missing/stale sampler is visible.

The root manager retains a private `usage-state.json` in its existing persistent
status directory (0600) and publishes only a safe `usage.json` (0644).
No message text, media, cookies, bot tokens or input events enter the ledger.
A manager restart resumes from that ledger without replaying previous counters.

Counter/boot resets and clock discontinuities do not subtract traffic or invent
a previous boot's usage. A sample crossing a UTC month boundary is apportioned
by time, and the previous month's totals are discarded. Brief gaps and the
time-apportioned traffic at a boundary make these estimates different from
Oracle's actual meters.

Monitoring failures are isolated from account reconciliation. Sampling does not
create traffic, keep an instance artificially busy, stop a worker or enforce a
spending cap. Message retention remains 72 hours.

## Oracle account totals

Provisioned block storage, backups, object storage and object request totals are
not inferred from the local filesystem. Enter these optional account-wide values
from Oracle Console. Blank means unknown, and the page labels them as manual
with the date they were saved. Monthly object-request values expire from view
at the next UTC month; storage/backups are point-in-time inventory.

Other Oracle services, other compute instances, trial credits and actual charges
must be checked in Oracle Console. This monitor is not an Oracle bill and cannot
guarantee that an account is free of charges.

## Updating an existing server

Update the release's `dashboard/*.py`, templates and static assets under
`/opt/messenger-dashboard/app/dashboard`, then restart both services:

```bash
sudo systemctl restart messenger-dashboard-web messenger-dashboard-manager
```

The manager reconciles existing labelled workers; a usage/settings change does
not change their account revision or require restarting them. No new Python
dependencies, database migration or native image rebuild are needed.

Allow about a minute for a first sample, then enter the allocated OCPUs/memory.
A second sample enables CPU/rate observations. If readings remain stale, inspect:

```bash
sudo journalctl -u messenger-dashboard-manager -n 50 --no-pager
```

## Sources

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle cloud price list — networking](https://www.oracle.com/cloud/price-list/)
- [Oracle Console](https://cloud.oracle.com/)
