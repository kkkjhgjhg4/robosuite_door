import torch
import torch.nn.functional as F
import numpy as np
from buffer import ReplayBuffer
from networks import CriticNetwork, ActorNetwork


class Agent:

    def __init__(self, actor_learning_rate, critic_learning_rate, input_dims, tau, env, gamma=0.99,
                 update_actor_interval=2, warmup=1000,
                 n_actions=2, max_size=1000000, layer1_size=256, layer2_size=128, batch_size=100, noise=0.1):
        self.gamma = gamma
        self.tau = tau
        self.max_action = env.action_space.high
        self.min_action = env.action_space.low
        self.memory = ReplayBuffer(max_size, input_dims, n_actions)
        self.batch_size = batch_size
        self.learn_step_counter = 0
        self.time_step = 0
        self.warmup = warmup
        self.n_actions = n_actions
        self.update_action_iter = update_actor_interval

        # Create networks
        self.actor = ActorNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                  fc2_dims=layer2_size, n_actions=n_actions,
                                  name='actor', learning_rate=actor_learning_rate)
        self.critic_1 = CriticNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                      fc2_dims=layer2_size, n_actions=n_actions,
                                      name='critic_1', learning_rate=critic_learning_rate)
        self.critic_2 = CriticNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                      fc2_dims=layer2_size, n_actions=n_actions,
                                      name='critic_2', learning_rate=critic_learning_rate)

        # Create the target networks
        self.target_actor = ActorNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                         fc2_dims=layer2_size, n_actions=n_actions,
                                         name='target_actor', learning_rate=actor_learning_rate)
        self.target_critic_1 = CriticNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                             fc2_dims=layer2_size, n_actions=n_actions,
                                             name='target_critic_1', learning_rate=critic_learning_rate)
        self.target_critic_2 = CriticNetwork(input_dims=input_dims, fc1_dims=layer1_size,
                                             fc2_dims=layer2_size, n_actions=n_actions,
                                             name='target_critic_2', learning_rate=critic_learning_rate)

        self.noise = noise
        self.update_network_parameters(tau=1)

    def choose_action(self, observation, validation=False):
        if self.time_step < self.warmup and not validation:
            mu = torch.tensor(np.random.normal(scale=self.noise, size=(self.n_actions,))).to(self.actor.device)
        else:
            state = torch.tensor(observation, dtype=torch.float).unsqueeze(0).to(self.actor.device)  # Shape: (1, 46)
            mu = self.actor.forward(state).squeeze(0).to(self.actor.device)  # Shape: (2,)

        mu_prime = mu + torch.tensor(np.random.normal(scale=self.noise), dtype=torch.float).to(self.actor.device)
        mu_prime = torch.clamp(mu_prime, self.min_action[0], self.max_action[0])

        self.time_step += 1

        return mu_prime.cpu().detach().numpy()

    def remember(self, state, action, reward, next_state, done):
        self.memory.store_transition(state, action, reward, next_state, done)

    def learn(self):
        if self.memory.mem_counter < self.batch_size * 10:
            return

        state, action, reward, next_state, done = self.memory.sample_buffer(self.batch_size)

        reward = torch.tensor(reward, dtype=torch.float).to(self.critic_1.device)
        done = torch.tensor(done).to(self.critic_1.device)
        next_state = torch.tensor(next_state, dtype=torch.float).to(self.critic_1.device)
        state = torch.tensor(state, dtype=torch.float).to(self.critic_1.device)
        action = torch.tensor(action, dtype=torch.float).to(self.critic_1.device)

        target_actions = self.target_actor.forward(next_state)


        target_actions = target_actions + torch.clamp(torch.tensor(np.random.normal(scale=0.2)), -0.5, 0.5)
        target_actions = torch.clamp(target_actions, self.min_action[0], self.max_action[0])

        next_q1 = self.target_critic_1.forward(next_state, target_actions)
        next_q2 = self.target_critic_2.forward(next_state, target_actions)

        q1 = self.critic_1.forward(state, action)
        q2 = self.critic_2.forward(state, action)

        next_q1[done] = 0.0
        next_q2[done] = 0.0

        next_q1 = next_q1.view(-1)
        next_q2 = next_q2.view(-1)

        next_critic_value = torch.min(next_q1, next_q2)

        target = reward + self.gamma * next_critic_value
        target = target.view(self.batch_size, 1)

        self.critic_1.optimizer.zero_grad()
        self.critic_2.optimizer.zero_grad()

        q1_loss = F.mse_loss(target, q1)
        q2_loss = F.mse_loss(target, q2)

        critic_loss = q1_loss + q2_loss
        critic_loss.backward()

        self.critic_1.optimizer.step()
        self.critic_2.optimizer.step()

        self.learn_step_counter += 1

        if self.learn_step_counter % self.update_action_iter != 0:
            return

        self.actor.optimizer.zero_grad()
        actor_q1_loss = self.critic_1.forward(state, self.actor.forward(state))
        actor_loss = - torch.mean(actor_q1_loss)  # to maximize, we add - before
        actor_loss.backward()

        self.actor.optimizer.step()
        self.update_network_parameters()

    def update_network_parameters(self, tau=None):
        if tau is None:
            tau = self.tau

        actor_state_dict = self.actor.state_dict()
        critic_1_state_dict = self.critic_1.state_dict()
        critic_2_state_dict = self.critic_2.state_dict()
        target_actor_state_dict = self.target_actor.state_dict()
        target_critic_1_state_dict = self.target_critic_1.state_dict()
        target_critic_2_state_dict = self.target_critic_2.state_dict()

        for name in critic_1_state_dict:
            critic_1_state_dict[name] = tau * critic_1_state_dict[name].clone() + (1 - tau) * target_critic_1_state_dict[name].clone()

        for name in critic_2_state_dict:
            critic_2_state_dict[name] = tau * critic_2_state_dict[name].clone() + (1 - tau) * target_critic_2_state_dict[name].clone()

        for name in actor_state_dict:
            actor_state_dict[name] = tau * actor_state_dict[name].clone() + (1 - tau) * target_actor_state_dict[name].clone()

        self.target_critic_1.load_state_dict(critic_1_state_dict)
        self.target_critic_2.load_state_dict(critic_2_state_dict)
        self.target_actor.load_state_dict(actor_state_dict)

    def save_models(self):
        self.actor.save_checkpoint()
        self.target_actor.save_checkpoint()
        self.critic_1.save_checkpoint()
        self.critic_2.save_checkpoint()
        self.target_critic_1.save_checkpoint()
        self.target_critic_2.save_checkpoint()

    def load_models(self):
        try:
            self.actor.load_checkpoint()
            self.target_actor.load_checkpoint()
            self.critic_1.load_checkpoint()
            self.critic_2.load_checkpoint()
            self.target_critic_1.load_checkpoint()
            self.target_critic_2.load_checkpoint()
            print("Successfully Loaded Models")
        except:
            print("Failed to Load Models. Staring from Scratch")