# Copyright 2023 OmniSafeAI Team. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================


from __future__ import annotations

from abc import ABC, abstractmethod
from multiprocessing import Pipe, Process

import numpy as np
from gymnasium.vector.vector_env import VectorEnv
from gymnasium.spaces import Box

from safety_gymnasium.vector.utils.tile_images import tile_images
from safety_gymnasium.tasks.safe_multi_agent.safe_mujoco_multi import SafeMAEnv
from typing import Optional


class ShareEnv(SafeMAEnv):
    """
    A environment wrapper to provide shared observation for multi-agent environment.
    """

    def __init__(
        self,
        scenario: str,
        agent_conf: str | None,
        agent_obsk: int | None = 1,
        agent_factorization: dict | None = None,
        local_categories: list[list[str]] | None = None,
        global_categories: tuple[str, ...] | None = None,
        render_mode: str | None = None,
        **kwargs,
    ):
        """Init.

        Args:
            scenario: The Task/Environment, valid values:
                "Ant", "HalfCheetah", "Hopper", "HumanoidStandup", "Humanoid", "Reacher", "Swimmer", "Pusher", "Walker2d", "InvertedPendulum", "InvertedDoublePendulum", "ManySegmentSwimmer", "ManySegmentAnt", "CoupledHalfCheetah"
            agent_conf: '${Number Of Agents}x${Number Of Segments per Agent}${Optionally Additional options}', eg '1x6', '2x4', '2x4d',
                If it set to None the task becomes single agent (the agent observes the entire environment, and performs all the actions)
            agent_obsk: Number of nearest joints to observe,
                If set to 0 it only observes local state,
                If set to 1 it observes local state + 1 joint over,
                If set to 2 it observes local state + 2 joints over,
                If it set to None the task becomes single agent (the agent observes the entire environment, and performs all the actions)
                The Default value is: 1
            agent_factorization: A custom factorization of the MuJoCo environment (overwrites agent_conf),
                see DOC [how to create new agent factorizations](https://robotics.farama.org/envs/MaMuJoCo/index.html#how-to-create-new-agent-factorizations).
            local_categories: The categories of local observations for each observation depth,
                It takes the form of a list where the k-th element is the list of observable items observable at the k-th depth
                For example: if it is set to `[["qpos, qvel"], ["qvel"]]` then means each agent observes its own position and velocity elements, and it's neighbors velocity elements.
                The default is: Check each environment's page on the "observation space" section.
            global_categories: The categories of observations extracted from the global observable space,
                For example: if it is set to `("qpos")` out of the globally observable items of the environment, only the position items will be observed.
                The default is: Check each environment's page on the "observation space" section.
            render_mode: see [Gymansium/MuJoCo](https://gymnasium.farama.org/environments/mujoco/),
                valid values: 'human', 'rgb_array', 'depth_array'
            kwargs: Additional arguments passed to the [Gymansium/MuJoCo](https://gymnasium.farama.org/environments/mujoco/) environment,
                Note: arguments that change the observation space will not work.

            Raises: NotImplementedError: When the scenario is not supported (not part of of the valid values)
        """
        super().__init__(
            scenario=scenario,
            agent_conf=agent_conf,
            agent_obsk=agent_obsk,
            agent_factorization=agent_factorization,
            local_categories=local_categories,
            global_categories=global_categories,
            render_mode=render_mode,
            **kwargs,
        )
        self.n_agents = len(self.agent_action_partitions)
        self.n_actions = max([len(l) for l in self.agent_action_partitions])
        self.share_obs_size = self._get_share_obs_size()
        self.obs_size=self._get_obs_size()
        self.share_observation_spaces = {}
        self.observation_spaces={}
        for agent in range(self.n_agents):
            self.share_observation_spaces[f"agent_{agent}"] = Box(low=-10, high=10, shape=(self.share_obs_size,)) 
            self.observation_spaces[f"agent_{agent}"] = Box(low=-10, high=10, shape=(self.obs_size,)) 

    def _get_obs(self):
        """Returns all agent observat3ions in a list"""
        state = self.env.state()
        obs_n = []
        for a in range(self.n_agents):
            agent_id_feats = np.zeros(self.n_agents, dtype=np.float32)
            agent_id_feats[a] = 1.0
            obs_i = np.concatenate([state, agent_id_feats])
            obs_i = (obs_i - np.mean(obs_i)) / np.std(obs_i)
            obs_n.append(obs_i)
        return obs_n

    def _get_obs_size(self):
        """Returns the shape of the observation"""
        return len(self._get_obs()[0])

    def _get_share_obs(self):
        # TODO: May want global states for different teams (so cannot see what the other team is communicating e.g.)
        state = self.env.state()
        state_normed = (state - np.mean(state)) / (np.std(state)+1e-8)
        share_obs = []
        for _ in range(self.n_agents):
            share_obs.append(state_normed)
        return share_obs

    def _get_share_obs_size(self):
        """Returns the shape of the share observation"""
        return len(self._get_share_obs()[0])

    def _get_avail_actions(self):
        """All actions are always available"""
        return np.ones(
            shape=(
                self.n_agents,
                self.n_actions,
            )
        )

    def reset(self, seed=None):
        """Reset the environment."""
        super().reset(seed=seed)
        return self._get_obs(), self._get_share_obs(), self._get_avail_actions()

    
    def step(
        self, actions: dict[str, np.ndarray]
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, str],
    ]:
        """Runs one timestep of the environment using the agents's actions.

        Note: if step is called after the agents have terminated/truncated the envrioment will continue to work as normal
        Args:
            actions:
                the actions of all agents

        Returns:
            see pettingzoo.utils.env.ParallelEnv.step() doc
        """
        dict_actions={}
        for agent_id, agent in enumerate(self.possible_agents):
            dict_actions[agent]=actions[agent_id]
        observations, rewards, costs, terminations, truncations, infos = super().step(dict_actions)
        dones={}
        for agent_id, agent in enumerate(self.possible_agents):
            dones[agent] = terminations[agent] or truncations[agent]
            rewards[agent] = [rewards[agent]]
            costs[agent]=[costs[agent]]
        observations, rewards, costs, dones, infos = list(observations.values()), list(rewards.values()), list(costs.values()), list(dones.values()), list(infos.values())
        return self._get_obs(), self._get_share_obs(), rewards, costs, dones, infos, self._get_avail_actions()


class CloudpickleWrapper:
    """
    Uses cloudpickle to serialize contents (otherwise multiprocessing tries to use pickle)
    """

    def __init__(self, x):
        self.x = x

    def __getstate__(self):
        import cloudpickle

        return cloudpickle.dumps(self.x)

    def __setstate__(self, ob):
        import pickle

        self.x = pickle.loads(ob)


class ShareVecEnv(ABC):
    """
    An abstract asynchronous, vectorized environment.
    Used to batch data from multiple copies of an environment, so that
    each observation becomes an batch of observations, and expected action is a batch of actions to
    be applied per-environment.
    """

    closed = False
    viewer = None

    metadata = {'render.modes': ['human', 'rgb_array']}

    def __init__(self, num_envs, observation_space, share_observation_space, action_space):
        self.num_envs = num_envs
        self._observation_space = observation_space
        self._share_observation_space = share_observation_space
        self._action_space = action_space

    @property
    def observation_space(self, idx: Optional[int] = None):
        if idx is None:
            return list(self._observation_space.values())
        return self._observation_space[f"agent_{idx}"]
    
    @property
    def share_observation_space(self, idx: Optional[int] = None):
        if idx is None:
            return list(self._share_observation_space.values())
        return self._share_observation_space[f"agent_{idx}"]
    
    @property
    def action_space(self, idx: Optional[int] = None):
        if idx is None:
            return list(self._action_space.values())
        return self._action_space[f"agent_{idx}"]

    @abstractmethod
    def reset(self):
        """
        Reset all the environments and return an array of
        observations, or a dict of observation arrays.

        If step_async is still doing work, that work will
        be cancelled and step_wait() should not be called
        until step_async() is invoked again.
        """
        pass

    @abstractmethod
    def step_async(self, actions):
        """
        Tell all the environments to start taking a step
        with the given actions.
        Call step_wait() to get the results of the step.

        You should not call this if a step_async run is
        already pending.
        """
        pass

    @abstractmethod
    def step_wait(self):
        """
        Wait for the step taken with step_async().

        Returns (obs, rews, cos, dones, infos):
         - obs: an array of observations, or a dict of
                arrays of observations.
         - rews: an array of rewards
         - cos: an array of costs
         - dones: an array of "episode done" booleans
         - infos: a sequence of info objects
        """
        pass

    def close_extras(self):
        """
        Clean up the  extra resources, beyond what's in this base class.
        Only runs when not self.closed.
        """
        pass

    def close(self):
        if self.closed:
            return
        if self.viewer is not None:
            self.viewer.close()
        self.close_extras()
        self.closed = True

    def step(self, actions):
        """
        Step the environments synchronously.

        This is available for backwards compatibility.
        """
        self.step_async(actions)
        return self.step_wait()

    def render(self, mode='human'):
        imgs = self.get_images()
        bigimg = tile_images(imgs)
        if mode == 'human':
            self.get_viewer().imshow(bigimg)
            return self.get_viewer().isopen
        elif mode == 'rgb_array':
            return bigimg
        else:
            raise NotImplementedError

    def get_images(self):
        """
        Return RGB images from each environment
        """
        raise NotImplementedError

    @property
    def unwrapped(self):
        if isinstance(self, VectorEnv):
            return self.venv.unwrapped
        else:
            return self

    def get_viewer(self):
        if self.viewer is None:
            from gymnasium.envs.classic_control import rendering

            self.viewer = rendering.SimpleImageViewer()
        return self.viewer



def shareworker(remote, parent_remote, env_fn_wrapper):
    parent_remote.close()
    env = env_fn_wrapper.x()
    while True:
        cmd, data = remote.recv()
        if cmd == 'step':
            ob, s_ob, reward, cost, done, info, available_actions = env.step(data)
            if 'bool' in done.__class__.__name__:
                if done:
                    ob, s_ob, available_actions = env.reset()
            else:
                if np.all(done):
                    ob, s_ob, available_actions = env.reset()

            remote.send((ob, s_ob, reward, cost, done, info, available_actions))
        elif cmd == 'reset':
            ob, s_ob, available_actions = env.reset()
            remote.send((ob, s_ob, available_actions))
        elif cmd == 'reset_task':
            ob = env.reset_task()
            remote.send(ob)
        elif cmd == 'render':
            if data == 'rgb_array':
                fr = env.render(mode=data)
                remote.send(fr)
            elif data == 'human':
                env.render(mode=data)
        elif cmd == 'close':
            env.close()
            remote.close()
            break
        elif cmd == 'get_spaces':
            remote.send((env.observation_spaces, env.share_observation_spaces, env.action_spaces))
        elif cmd == 'render_vulnerability':
            fr = env.render_vulnerability(data)
            remote.send(fr)
        elif cmd == 'get_num_agents':
            remote.send(env.n_agents)
        else:
            raise NotImplementedError


class ShareSubprocVecEnv(ShareVecEnv):
    def __init__(self, env_fns, spaces=None):
        """
        envs: list of gym environments to run in subprocesses
        """
        self.waiting = False
        self.closed = False
        nenvs = len(env_fns)
        self.remotes, self.work_remotes = zip(*[Pipe() for _ in range(nenvs)])
        self.ps = [
            Process(target=shareworker, args=(work_remote, remote, CloudpickleWrapper(env_fn)))
            for (work_remote, remote, env_fn) in zip(self.work_remotes, self.remotes, env_fns)
        ]
        for p in self.ps:
            p.daemon = True  # if the main process crashes, we should not cause things to hang
            p.start()
        for remote in self.work_remotes:
            remote.close()
        self.remotes[0].send(('get_num_agents', None))
        self.n_agents = self.remotes[0].recv()
        self.remotes[0].send(('get_spaces', None))
        observation_space, share_observation_space, action_space = self.remotes[0].recv()
        # print("wrapper:", share_observation_space)
        ShareVecEnv.__init__(
            self, len(env_fns), observation_space, share_observation_space, action_space
        )

    def step_async(self, actions):
        for remote, action in zip(self.remotes, actions):
            remote.send(('step', action))
        self.waiting = True

    def step_wait(self):
        results = [remote.recv() for remote in self.remotes]
        self.waiting = False
        obs, share_obs, rews, costs, dones, infos, available_actions = zip(*results)

        # cost_x = np.array([item[0]['cost'] for item in infos])
        # print("=====cost_x=====: ", cost_x.sum())
        # print("=====np.stack(dones)=====: ", np.stack(dones))
        return (
            np.stack(obs),
            np.stack(share_obs),
            np.stack(rews),
            np.stack(costs),
            np.stack(dones),
            infos,
            np.stack(available_actions),
        )

    def reset(self):
        for remote in self.remotes:
            remote.send(('reset', None))
        results = [remote.recv() for remote in self.remotes]
        obs, share_obs, available_actions = zip(*results)
        return np.stack(obs), np.stack(share_obs), np.stack(available_actions)

    def reset_task(self):
        for remote in self.remotes:
            remote.send(('reset_task', None))
        return np.stack([remote.recv() for remote in self.remotes])

    def close(self):
        if self.closed:
            return
        if self.waiting:
            for remote in self.remotes:
                remote.recv()
        for remote in self.remotes:
            remote.send(('close', None))
        for p in self.ps:
            p.join()
        self.closed = True

class ShareDummyVecEnv(ShareVecEnv):
    def __init__(self, env_fns):
        self.envs = [fn() for fn in env_fns]
        env = self.envs[0]
        self.n_agents=env.n_agents
        ShareVecEnv.__init__(
            self, len(env_fns), env.observation_spaces, env.share_observation_spaces, env.action_spaces
        )
        self.actions = None

    def step_async(self, actions):
        self.actions = actions

    def step_wait(self):
        results = [env.step(a) for (a, env) in zip(self.actions, self.envs)]
        obs, share_obs, rews, cos, dones, infos, available_actions = map(np.array, zip(*results))

        for i, done in enumerate(dones):
            if 'bool' in done.__class__.__name__:
                if done:
                    obs[i], share_obs[i], available_actions[i] = self.envs[i].reset()
            else:
                if np.all(done):
                    obs[i], share_obs[i], available_actions[i] = self.envs[i].reset()
        self.actions = None

        return obs, share_obs, rews, cos, dones, infos, available_actions

    def reset(self):
        results = [env.reset() for env in self.envs]
        obs, share_obs, available_actions = map(np.array, zip(*results))
        return obs, share_obs, available_actions

    def close(self):
        for env in self.envs:
            env.close()

    def render(self):
        return self.envs[0].render()
