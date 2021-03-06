from random import uniform
import os
import json
import uuid
import multiprocessing
import signal
import threading
import asyncio
from datetime import timedelta
from functools import partial
from cloudevents.http import to_json, CloudEvent
from prefect import Flow
from prefect.engine.executors import DaskExecutor
from prefect import context as prefect_context
from dask_jobqueue.lsf import LSFJob
from ert_shared.ensemble_evaluator.entity import identifiers as ids
from ert_shared.ensemble_evaluator.prefect_ensemble.client import Client
import sys
from ert_shared.ensemble_evaluator.prefect_ensemble.storage_driver import (
    storage_driver_factory,
)
from ert_shared.ensemble_evaluator.prefect_ensemble.unix_step import UnixStep
from ert_shared.ensemble_evaluator.config import find_open_port
from ert_shared.ensemble_evaluator.entity.ensemble import (
    _Ensemble,
    _BaseJob,
    _Step,
    _Stage,
    _Realization,
)


async def _eq_submit_job(self, script_filename):
    with open(script_filename) as fh:
        lines = fh.readlines()[1:]
    lines = [
        line.strip() if "#BSUB" not in line else line[5:].strip() for line in lines
    ]
    piped_cmd = [self.submit_command + " ".join(lines)]
    return self._call(piped_cmd, shell=True)


def _get_executor(name="local"):
    if name == "local":
        return None
    elif name == "lsf":
        LSFJob._submit_job = _eq_submit_job
        cluster_kwargs = {
            "queue": "mr",
            "project": None,
            "cores": 1,
            "memory": "1GB",
            "use_stdin": True,
            "n_workers": 2,
            "silence_logs": "debug",
            "scheduler_options": {"port": find_open_port(lower=51820, upper=51840)},
        }
        return DaskExecutor(
            cluster_class="dask_jobqueue.LSFCluster",
            cluster_kwargs=cluster_kwargs,
            debug=True,
        )
    elif name == "pbs":
        cluster_kwargs = {
            "n_workers": 10,
            "queue": "normal",
            "project": "ERT-TEST",
            "local_directory": "$TMPDIR",
            "cores": 4,
            "memory": "16GB",
            "resource_spec": "select=1:ncpus=4:mem=16GB",
        }
        return DaskExecutor(
            cluster_class="dask_jobqueue.PBSCluster",
            cluster_kwargs=cluster_kwargs,
            debug=True,
        )
    else:
        raise ValueError(f"Unknown executor name {name}")


def gen_coef(parameters, real, config):
    data = {}
    paths = {}
    storage = storage_driver_factory(config.get("storage"), "coeff")
    for iens in range(real):
        for name, elements in parameters.items():
            for element in elements:
                start, end = element["args"]
                data[element["name"]] = uniform(start, end)
        os.makedirs(f"coeff", exist_ok=True)
        file_name = f"coeffs.json"
        file_path = os.path.join("coeff", file_name)
        with open(file_path, "w") as f:
            json.dump(data, f)
        paths[iens] = (storage.store(file_name, iens),)
    return paths


class PrefectEnsemble(_Ensemble):
    def __init__(self, config):
        self.config = config
        self._ee_dispach_url = None
        self._reals = self._get_reals()
        self._eval_proc = None
        self._ee_id = None
        super().__init__(self._reals, metadata={"iter": 0})

    def _get_reals(self):
        reals = []
        for iens in range(self.config["realizations"]):
            stages = []
            for stage in self.config["stages"]:
                steps = []
                stage_id = uuid.uuid4()
                for step in stage["steps"]:
                    jobs = []
                    for job in step["jobs"]:
                        job_id = uuid.uuid4()
                        jobs.append(_BaseJob(id_=str(job_id), name=job["name"]))
                    step_id = uuid.uuid4()
                    steps.append(
                        _Step(
                            id_=str(step_id),
                            inputs=step.get("inputs", []),
                            outputs=step["outputs"],
                            jobs=jobs,
                            name=step["name"],
                        )
                    )

                stages.append(
                    _Stage(
                        id_=str(stage_id),
                        steps=steps,
                        status="Unknown",
                        name=stage["name"],
                    )
                )
            reals.append(_Realization(iens=iens, stages=stages, active=True))
        return reals

    @staticmethod
    def _on_task_failure(task, state, url):
        if prefect_context.task_run_count > task.max_retries:
            with Client(url) as c:
                event = CloudEvent(
                    {
                        "type": ids.EVTYPE_FM_STEP_FAILURE,
                        "source": f"/ert/ee/{task.get_ee_id()}/real/{task.get_iens()}/stage/{task.get_stage_id()}/step/{task.get_step_id()}",
                        "datacontenttype": "application/json",
                    },
                    {"stderr": state.message},
                )
                c.send(to_json(event).decode())

    def get_id(self, iens, stage_name, step_name=None, job_index=None):
        real = next(real for real in self._reals if real.get_iens() == iens)
        stage = next(
            stage for stage in real.get_stages() if stage.get_name() == stage_name
        )
        if step_name is None:
            return stage.get_id()
        step = next(step for step in stage.get_steps() if step.get_name() == step_name)
        if job_index is None:
            return step.get_id()
        job = step.get_jobs()[job_index]
        return job.get_id()

    def get_ordering(self, iens):
        table_of_elements = []
        for stage in self.config["stages"]:
            for step in stage["steps"]:
                jobs = [
                    {
                        "id": self.get_id(
                            iens,
                            stage_name=stage["name"],
                            step_name=step["name"],
                            job_index=idx,
                        ),
                        "name": job.get("name"),
                        "executable": job.get("executable"),
                        "args": job.get("args", []),
                    }
                    for idx, job in enumerate(step.get("jobs", []))
                ]
                table_of_elements.append(
                    {
                        "iens": iens,
                        "stage_name": stage["name"],
                        "stage_id": self.get_id(iens, stage_name=stage["name"]),
                        "step_id": self.get_id(
                            iens, stage_name=stage["name"], step_name=step["name"]
                        ),
                        **step,
                        "jobs": jobs,
                    }
                )

        produced = set()
        ordering = []
        while table_of_elements:
            temp_list = produced.copy()
            for element in table_of_elements:
                if set(element.get("inputs", [])).issubset(temp_list):
                    ordering.append(element)
                    produced = produced.union(set(element["outputs"]))
                    table_of_elements.remove(element)
        return ordering

    def get_flow(self, ee_id, dispatch_url, input_files, real_range):
        with Flow(f"Realization range {real_range}") as flow:
            for iens in real_range:
                output_to_res = {}
                for step in self.get_ordering(iens=iens):
                    inputs = [
                        output_to_res.get(input, []) for input in step.get("inputs", [])
                    ]
                    stage_task = UnixStep(
                        resources=list(input_files[iens])
                        + self.store_resources(step["resources"]),
                        outputs=step.get("outputs", []),
                        job_list=step.get("jobs", []),
                        iens=iens,
                        cmd="python3",
                        url=dispatch_url,
                        step_id=step["step_id"],
                        stage_id=step["stage_id"],
                        ee_id=ee_id,
                        on_failure=partial(self._on_task_failure, url=dispatch_url),
                        run_path=self.config.get("run_path"),
                        storage_config=self.config.get("storage"),
                        max_retries=self.config.get("max_retries", 2),
                        retry_delay=timedelta(seconds=2)
                        if self.config.get("max_retries") > 0
                        else None,
                    )
                    result = stage_task(expected_res=inputs)

                    for output in step.get("outputs", []):
                        output_to_res[output] = result["outputs"]
        return flow

    def evaluate(self, config, ee_id):
        self._ee_dispach_url = config.get("dispatch_url")
        self._ee_id = ee_id
        self._eval_proc = multiprocessing.Process(
            target=self._evaluate,
            args=(self._ee_dispach_url, ee_id),
        )
        self._eval_proc.daemon = True
        self._eval_proc.start()

    def _fetch_input_files(self):
        num_realizations = self.config["realizations"]
        input_files = {iens: () for iens in range(num_realizations)}

        if "parameters" in self.config.keys():
            parameter_files = gen_coef(
                self.config["parameters"], num_realizations, self.config
            )
            for iens, files in parameter_files.items():
                input_files[iens] += files

        if "input_files" in self.config.keys():
            for iens, files in self.config["input_files"].items():
                input_files[iens] += files

        return input_files

    def _evaluate(self, dispatch_url, ee_id):
        sys.stderr = open("prefect.log", "w")

        input_files = self._fetch_input_files()

        with Client(dispatch_url) as c:
            event = CloudEvent(
                {
                    "type": ids.EVTYPE_ENSEMBLE_STARTED,
                    "source": f"/ert/ee/{self._ee_id}",
                    "datacontenttype": "application/json",
                },
            )
            c.send(to_json(event).decode())
        self.run_flow(ee_id, dispatch_url, input_files)

        with Client(dispatch_url) as c:
            event = CloudEvent(
                {
                    "type": ids.EVTYPE_ENSEMBLE_STOPPED,
                    "source": f"/ert/ee/{self._ee_id}",
                    "datacontenttype": "application/json",
                },
            )
            c.send(to_json(event).decode())

    def run_flow(self, ee_id, dispatch_url, input_files):
        real_per_batch = self.config["max_running"]
        real_range = range(self.config["realizations"])
        i = 0
        while i < self.config["realizations"]:
            realization_range = real_range[i : i + real_per_batch]
            flow = self.get_flow(ee_id, dispatch_url, input_files, realization_range)
            flow.run(executor=_get_executor(self.config["executor"]))
            i = i + real_per_batch

    def store_resources(self, resources):
        storage = storage_driver_factory(
            self.config.get("storage"), self.config.get("config_path", ".")
        )
        stored_resources = [storage.store(res) for res in resources]
        return stored_resources

    def is_cancellable(self):
        return True

    def cancel(self):
        threading.Thread(target=self._cancel).start()

    def _cancel(self):
        if self._eval_proc is not None:
            os.kill(self._eval_proc.pid, signal.SIGINT)
        self._eval_proc = None
        event = CloudEvent(
            {
                "type": ids.EVTYPE_ENSEMBLE_CANCELLED,
                "source": f"/ert/ee/{self._ee_id}",
                "datacontenttype": "application/json",
            },
        )

        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.send_cloudevent(self._ee_dispach_url, event))
        loop.close()
