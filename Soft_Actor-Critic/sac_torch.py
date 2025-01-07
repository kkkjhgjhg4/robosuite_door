import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Normal
import numpy as np
from sac_networks import ActorNetwork, CriticNetwork
from sac_buffer import ReplayBuffer
import torch.nn.functional as F


class SACAgent:
    def __init__(self,
                 input_dims,
                 n_actions,
                 alpha=0.4,
                 gamma=0.99,
                 tau=0.005,
                 lr_actor=3e-4,
                 lr_critic=3e-4,
                 lr_alpha=0.001,
                 max_size=1000000,
                 batch_size=256,
                 device=torch.device('cpu'),
                 action_scale=1.0,
                 action_bias=0.0,
                 target_entropy=None):
        self.gamma = gamma
        self.tau = tau
        self.batch_size = batch_size
        self.device = device

        # Entropy coefficient
        if target_entropy is None:
            # Heuristic for target entropy: -dim(A)
            target_entropy = -n_actions
        self.target_entropy = target_entropy

        # Initialize log_alpha as a learnable parameter
        self.log_alpha = torch.zeros(1, requires_grad=True, device=self.device)
        self.alpha_optimizer = optim.Adam([self.log_alpha], lr=lr_alpha)

        self.alpha = alpha  # Initial entropy coefficient (can be overridden by auto-tuning)

        # Initialize Actor network
        self.actor = ActorNetwork(input_dims, n_actions=n_actions).to(self.device)
        self.actor_optimizer = optim.Adam(self.actor.parameters(), lr=lr_actor)

        # Initialize Critic networks
        self.critic = CriticNetwork(input_dims, n_actions=n_actions).to(self.device)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=lr_critic)

        # Initialize target Critic networks
        self.critic_target = CriticNetwork(input_dims, n_actions=n_actions).to(self.device)
        self.critic_target.load_state_dict(self.critic.state_dict())

        # Initialize Replay Buffer
        self.memory = ReplayBuffer(max_size, input_dims, n_actions, device=self.device)

        # Action scaling parameters
        self.action_scale = torch.tensor(action_scale, dtype=torch.float32).to(self.device)
        self.action_bias = torch.tensor(action_bias, dtype=torch.float32).to(self.device)

    def choose_action(self, state):
        state = torch.FloatTensor(state).unsqueeze(0).to(self.device)
        mean, std = self.actor(state)
        dist = Normal(mean, std)
        x_t = dist.rsample()  # For reparameterization trick (mean + std * N(0,1))
        y_t = torch.tanh(x_t)
        # Scale action to environment's action space
        scaled_action = y_t * self.action_scale + self.action_bias
        action = scaled_action.cpu().detach().numpy()[0]

        # Compute log_prob
        log_prob = dist.log_prob(x_t)
        # Enforcing Action Bound
        log_prob -= torch.log(self.action_scale * (1 - y_t.pow(2)) + 1e-6)
        log_prob = log_prob.sum(dim=-1)
        return action, log_prob.cpu().detach().numpy()

    def store_transition(self, state, action, reward, state_, done):
        # Store scaled actions in the buffer
        self.memory.store_transition(state, action, reward, state_, done)

    def sample_memory(self):
        states, actions, rewards, states_, dones = self.memory.sample_buffer(self.batch_size)
        return states, actions, rewards, states_, dones

    def update(self):
        if len(self.memory) < self.batch_size:
            return

        states, actions, rewards, states_, dones = self.sample_memory()

        # ---------------------------- update critic ---------------------------- #
        with torch.no_grad():
            # Compute target Q-values
            mean, std = self.actor(states_)
            dist = Normal(mean, std)
            x_t = dist.rsample()
            y_t = torch.tanh(x_t)
            # Scale actions
            scaled_actions = y_t * self.action_scale + self.action_bias
            log_prob = dist.log_prob(x_t)
            log_prob -= torch.log(self.action_scale * (1 - y_t.pow(2)) + 1e-6)
            log_prob = log_prob.sum(dim=-1, keepdim=True)

            target_Q1, target_Q2 = self.critic_target(states_, scaled_actions)
            target_Q = torch.min(target_Q1, target_Q2) - torch.exp(self.log_alpha) * log_prob
            target_Q = rewards + (1 - dones) * self.gamma * target_Q

        current_Q1, current_Q2 = self.critic(states, actions)
        critic_loss = F.mse_loss(current_Q1, target_Q) + F.mse_loss(current_Q2, target_Q)

        # Gradient Clipping
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.critic.parameters(), max_norm=1.0)
        self.critic_optimizer.step()


        # ---------------------------- update actor ---------------------------- #
        mean, std = self.actor(states)
        dist = Normal(mean, std)
        x_t = dist.rsample()
        y_t = torch.tanh(x_t)
        # Scale actions
        scaled_actions = y_t * self.action_scale + self.action_bias
        log_prob = dist.log_prob(x_t)
        log_prob -= torch.log(self.action_scale * (1 - y_t.pow(2)) + 1e-6)
        log_prob = log_prob.sum(dim=-1, keepdim=True)

        Q1_new, Q2_new = self.critic(states, scaled_actions)
        Q_new = torch.min(Q1_new, Q2_new)

        actor_loss = (torch.exp(self.log_alpha) * log_prob - Q_new).mean()

        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.actor.parameters(), max_norm=1.0)
        self.actor_optimizer.step()

        # ----------------------- update alpha ----------------------- #
        # Compute alpha loss
        alpha_loss = -(self.log_alpha * (log_prob + self.target_entropy).detach()).mean()

        self.alpha_optimizer.zero_grad()
        alpha_loss.backward()
        self.alpha_optimizer.step()

        # Update alpha
        self.alpha = torch.exp(self.log_alpha)

        # ----------------------- update target networks ----------------------- #
        for param, target_param in zip(self.critic.parameters(), self.critic_target.parameters()):
            target_param.data.copy_(self.tau * param.data + (1 - self.tau) * target_param.data)

    def save_models(self):
        torch.save(self.actor.state_dict(), 'tmp/sac/actor.pth')
        torch.save(self.critic.state_dict(), 'tmp/sac/critic.pth')
        torch.save(self.critic_target.state_dict(), 'tmp/sac/critic_target.pth')
        torch.save(self.log_alpha, 'tmp/sac/log_alpha.pth')
        print("SAC models and alpha saved successfully.")

    def load_models(self):
        try:
            self.actor.load_state_dict(torch.load('tmp/sac/actor.pth', map_location=self.device))
            self.critic.load_state_dict(torch.load('tmp/sac/critic.pth', map_location=self.device))
            self.critic_target.load_state_dict(torch.load('tmp/sac/critic_target.pth', map_location=self.device))
            self.log_alpha = torch.load('tmp/sac/log_alpha.pth', map_location=self.device)
            self.log_alpha.requires_grad = True  # Ensure gradients are enabled
            self.alpha = torch.exp(self.log_alpha)
            print("SAC models and alpha loaded successfully.")
        except:
            print("Failed to load SAC models and alpha. Starting from scratch.")