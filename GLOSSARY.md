# Project analysis

The app analyses a project's telemetry and presents findings from its configured sources.

## Language

**Project**:
A named analysis target with shared label matchers and up to one source of each kind.
_Avoid_: Workspace, environment

**Source**:
A project's connection to one telemetry provider, including its selection filters and optional credential reference. A source contributes data only while it is configured for the project.
_Avoid_: Integration, connector

**Scope**:
The filters selecting the telemetry included in an analysis. Prometheus custom rules use the project's label matchers as their scope.
_Avoid_: Query context

**Custom rule**:
A user-defined PromQL query and analysis policy attached to a project's Prometheus source.
_Avoid_: Alert rule (reserved for rules managed by Grafana)

**Connection test**:
A check of whether a source can be reached and its selected telemetry read. Its result is stale when the current connection settings or applicable scope differ from those tested; changing custom rules alone does not make it stale.
_Avoid_: Analysis, health report
