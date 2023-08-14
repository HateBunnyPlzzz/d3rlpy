import dataclasses
from typing import Dict

import torch
from torch import nn
from torch.optim import Optimizer

from ....dataset import Shape
from ....models.torch import DiscreteEnsembleQFunctionForwarder
from ....torch_utility import Modules, TorchMiniBatch, hard_sync, train_api
from ..base import QLearningAlgoImplBase
from .utility import DiscreteQFunctionMixin

__all__ = ["DQNImpl", "DQNModules", "DoubleDQNImpl"]


@dataclasses.dataclass(frozen=True)
class DQNModules(Modules):
    q_funcs: nn.ModuleList
    targ_q_funcs: nn.ModuleList
    optim: Optimizer


class DQNImpl(DiscreteQFunctionMixin, QLearningAlgoImplBase):
    _modules: DQNModules
    _gamma: float
    _q_func_forwarder: DiscreteEnsembleQFunctionForwarder
    _targ_q_func_forwarder: DiscreteEnsembleQFunctionForwarder

    def __init__(
        self,
        observation_shape: Shape,
        action_size: int,
        modules: DQNModules,
        q_func_forwarder: DiscreteEnsembleQFunctionForwarder,
        targ_q_func_forwarder: DiscreteEnsembleQFunctionForwarder,
        gamma: float,
        device: str,
    ):
        super().__init__(
            observation_shape=observation_shape,
            action_size=action_size,
            modules=modules,
            device=device,
        )
        self._gamma = gamma
        self._q_func_forwarder = q_func_forwarder
        self._targ_q_func_forwarder = targ_q_func_forwarder
        hard_sync(modules.targ_q_funcs, modules.q_funcs)

    @train_api
    def update(self, batch: TorchMiniBatch) -> Dict[str, float]:
        self._modules.optim.zero_grad()

        q_tpn = self.compute_target(batch)

        loss = self.compute_loss(batch, q_tpn)

        loss.backward()
        self._modules.optim.step()

        return {"loss": float(loss.cpu().detach().numpy())}

    def compute_loss(
        self,
        batch: TorchMiniBatch,
        q_tpn: torch.Tensor,
    ) -> torch.Tensor:
        return self._q_func_forwarder.compute_error(
            observations=batch.observations,
            actions=batch.actions.long(),
            rewards=batch.rewards,
            target=q_tpn,
            terminals=batch.terminals,
            gamma=self._gamma**batch.intervals,
        )

    def compute_target(self, batch: TorchMiniBatch) -> torch.Tensor:
        with torch.no_grad():
            next_actions = self._targ_q_func_forwarder.compute_expected_q(
                batch.next_observations
            )
            max_action = next_actions.argmax(dim=1)
            return self._targ_q_func_forwarder.compute_target(
                batch.next_observations,
                max_action,
                reduction="min",
            )

    def inner_predict_best_action(self, x: torch.Tensor) -> torch.Tensor:
        return self._q_func_forwarder.compute_expected_q(x).argmax(dim=1)

    def inner_sample_action(self, x: torch.Tensor) -> torch.Tensor:
        return self.inner_predict_best_action(x)

    def update_target(self) -> None:
        hard_sync(self._modules.targ_q_funcs, self._modules.q_funcs)

    @property
    def q_function(self) -> nn.ModuleList:
        return self._modules.q_funcs

    @property
    def q_function_optim(self) -> Optimizer:
        return self._modules.optim


class DoubleDQNImpl(DQNImpl):
    def compute_target(self, batch: TorchMiniBatch) -> torch.Tensor:
        with torch.no_grad():
            action = self.inner_predict_best_action(batch.next_observations)
            return self._targ_q_func_forwarder.compute_target(
                batch.next_observations,
                action,
                reduction="min",
            )
