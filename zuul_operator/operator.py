# Copyright 2021 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import asyncio
import collections
import yaml

import kopf
import pykube
import kubernetes

from . import objects
from . import utils
from . import certmanager
from .zuul import Zuul


ConfigResource = collections.namedtuple('ConfigResource', [
    'attr', 'namespace', 'zuul_name', 'resource_name'])


@kopf.on.startup()
def startup(memo, **kwargs):
    # (zuul_namespace, zuul) -> list of resources
    memo.config_resources = {}
    # lookup all zuuls and update configmaps

    api = pykube.HTTPClient(pykube.KubeConfig.from_env())
    for namespace in objects.Namespace.objects(api):
        for zuul in objects.ZuulObject.objects(api).filter(
                namespace=namespace.name):
            resources = memo.config_resources.\
                setdefault((namespace.name, zuul.name), [])
            # Zuul tenant config
            secret = zuul.obj['spec']['scheduler']['config']['secretName']
            res = ConfigResource('spec.scheduler.config.secretName',
                                 namespace.name, zuul.name, secret)
            resources.append(res)

            # Nodepool config
            secret = zuul.obj['spec']['launcher']['config']['secretName']
            res = ConfigResource('spec.launcher.config.secretName',
                                 namespace.name, zuul.name, secret)
            resources.append(res)


@kopf.on.update('secrets')
def update_secret(name, namespace, logger, memo, new, **kwargs):
    # if this configmap isn't known, ignore
    logger.info(f"Update secret {namespace}/{name}")

    api = pykube.HTTPClient(pykube.KubeConfig.from_env())
    for ((zuul_namespace, zuul_name), resources) in \
        memo.config_resources.items():
        for resource in resources:
            if (resource.namespace != namespace or
                resource.resource_name != name):
                continue
            logger.info(f"Affects zuul {zuul_namespace}/{zuul_name}")
            zuul_obj = objects.ZuulObject.objects(api).filter(
                namespace=zuul_namespace).get(name=zuul_name)
            zuul = Zuul(namespace, zuul_name, logger, zuul_obj.obj['spec'])
            if resource.attr == 'spec.scheduler.config.secretName':
                zuul.smart_reconfigure()
            if resource.attr == 'spec.launcher.config.secretName':
                zuul.create_nodepool()


@kopf.on.create('zuuls', backoff=10)
def create_fn(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create zuul {namespace}/{name}")

    zuul = Zuul(namespace, name, logger, spec)
    # Get DB installation started first; it's slow and has no
    # dependencies.
    zuul.install_db()
    # Install Cert-Manager and request the CA cert before installing
    # ZK because the CRDs must exist.
    zuul.install_cert_manager()
    zuul.wait_for_cert_manager()
    zuul.create_cert_manager_ca()
    # Now we can install ZK
    zuul.install_zk()
    # Wait for both to finish
    zuul.wait_for_zk()
    zuul.wait_for_db()

    zuul.write_zuul_conf()
    zuul.create_zuul()

    #return {'message': 'hello world'}  # will be the new status


@kopf.on.update('zuuls', backoff=10)
def update_fn(name, namespace, logger, old, new, **kwargs):
    logger.info(f"Update zuul {namespace}/{name}")

    old = old['spec']
    new = new['spec']

    zuul = Zuul(namespace, name, logger, new)
    conf_changed = False
    spec_changed = False
    if new.get('database') != old.get('database'):
        logger.info("Database changed")
        conf_changed = True
        # redo db stuff
        zuul.install_db()
        zuul.wait_for_db()

    if new.get('zookeeper') != old.get('zookeeper'):
        logger.info("ZooKeeper changed")
        conf_changed = True
        # redo zk
        zuul.install_cert_manager()
        zuul.wait_for_cert_manager()
        zuul.create_cert_manager_ca()
        # Now we can install ZK
        zuul.install_zk()
        zuul.wait_for_zk()
    if new.get('connections') != old.get('connections'):
        logger.info("Connections changed")
        conf_changed = True
    if new.get('imagePrefix') != old.get('imagePrefix'):
        logger.info("Image prefix changed")
        spec_changed = True
    for key in ['executor', 'merger', 'scheduler', 'registry',
                'launcher', 'connections', 'externalConfig']:
        if new.get(key) != old.get(key):
            logger.info(f"{key} changed")
            spec_changed = True

    if conf_changed:
        spec_changed = True
        zuul.write_zuul_conf()

    if spec_changed:
        zuul.create_zuul()


class ZuulOperator:
    def run(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(kopf.operator())
