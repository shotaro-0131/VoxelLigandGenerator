"""
reference 
    https://github.com/pbsinclair42/MCTS
"""
from __future__ import division

from numpy.lib.npyio import savez_compressed
from myMcts import MyMcts as mcts
# from mcts import mcts
import hydra
# from icecream import ic
from rdkit import rdBase
from rdkit.Chem import PandasTools, QED
from utils.preprocess import *
from utils import sascorer
from score import calc_vina_score
from os.path import exists

from openbabel import openbabel
class StateInterface():
    def getCurrentPlayer(self):
        # 1 for maximiser, -1 for minimiser
        raise NotImplementedError()

    def getPossibleActions(self):
        raise NotImplementedError()

    def takeAction(self, action):
        raise NotImplementedError()

    def isTerminal(self):
        raise NotImplementedError()

    def getReward(self):
        # only needed for terminal states
        raise NotImplementedError()

    def __eq__(self, other):
        raise NotImplementedError()


class ActionInterface():
    def __eq__(self, other):
        raise NotImplementedError

    def __hash__(self):
        raise NotImplementedError()

class InitAtomAdd(ActionInterface):
  def __init__(self, position, atom_type):
    self.position = position
    self.atom_type = atom_type

  def __eq__(self, other):
    return self.__class__ == other.__class__ and self.position == other.position and self.atom_type == other.atom_type

  def __hash__(self):
    return hash((self.position[0], self.position[1], self.position[2], self.atom_type, self.__class__))

class AtomAdd(ActionInterface):
  def __init__(self, position, atom_type, selected_index, bond, connected_index=None, connected_bond=None):
    self.position = position
    self.selected_index = selected_index
    self.atom_type = atom_type
    self.bond = bond
    
    self.connected_index = connected_index
    self.connected_bond = connected_bond

  def __eq__(self, other):
    return self.__class__ == other.__class__ and self.position == other.position and self.atom_type == other.atom_type and self.bond == other.bond

    # return self.__class__ == other.__class__ and self.position == other.position and self.atom_type == other.atom_type and self.selected_index == other.selected_index and self.bond == other.bond and self.connected_index == other.connected_index and self.connected_bond == other.connected_bond

  def __hash__(self):
    return hash((self.position[0], self.position[1], self.position[2], self.atom_type, self.bond, self.__class__, self.connected_index, self.connected_bond))

import numpy as np
import itertools

from copy import deepcopy
import math

class BondType:
    def __init__(self, atoms, min_len, max_len, hands):
        self.atoms = atoms
        # voxel base
        # self.min_len = (length-0.25*math.sqrt(3))
        # self.max_len = (length+0.25*math.sqrt(3))
        
        # openbabel base
        self.min_len = min_len
        self.max_len = max_len    
        self.hands = hands
        self.available_voxels = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if 0.25*dx*dx + 0.25*dy*dy + 0.25*dz*dz >= self.min_len*self.min_len and 0.25*dx*dx + 0.25*dy*dy + 0.25*dz*dz <= self.max_len*self.max_len]
        self.near_voxels = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if 0.25*dx*dx + 0.25*dy*dy + 0.25*dz*dz < self.min_len*self.min_len]
    def get(self, atom):
        if atom == self.atoms[0]:
            return self.atoms[1]
        else:
            return self.atoms[0]
    def isin(self, dist):
        return self.min_len <= dist and self.max_len >= dist
    def isOut(self, dist):
        return self.max_len < dist 
        
# CC1 = BondType([0,0], 1.54, 1)
# CC2 = BondType([0,0], 1.34, 2)
# CC3 = BondType([0,0], 1.20, 3)
# CO1 = BondType([0,1], 1.43, 1)
# CO2 = BondType([0,1], 1.22, 2)
# CN1 = BondType([0,2], 1.45, 1)
# CN2 = BondType([0,2], 1.28, 2)
# CN3 = BondType([0,2], 1.16, 3)

"""
https://cccbdb.nist.gov/expbondlengths1x.asp
"""
C1=0.75
C2=0.67
N1=0.71
N2=0.60
O1=0.63
O2=0.57
MARGIN=0.45
CC1 = BondType([0,0], C2+C1, C1+C1+MARGIN, 1)
CC2 = BondType([0,0], C2+C2-MARGIN, C2+C1, 2)
# CC3 = BondType([0,0], 1.20-0.45, 1.20, 3)
CO1 = BondType([0,1], (C2+O2+C1+O1)/2, C1+O1+MARGIN, 1)
CO2 = BondType([0,1], C2+O2-MARGIN, (C2+O2+C1+O1)/2, 2)
CN1 = BondType([0,2], (C2+N2+C1+N1)/2, C1+N1+MARGIN, 1)
CN2 = BondType([0,2], C2+N2-MARGIN, (C2+N2+C1+N1)/2, 2)
# CN3 = BondType([0,2], 1.16-0.45, 1.16, 3)

MAX_LENGTH=[C1+C1+MARGIN, C1+O1+MARGIN, C1+N1+MARGIN]
# BondTypes=[CC1, CC2, CC3, CO1, CO2, CN1, CN2, CN3]
BondTypes=[CC1,CC2,CO1,CO2,CN1,CN2]

import os 

class GridState(StateInterface):
  def __init__(self, target, use_knowledge):
    self.target=target
    self.use_knowledge=use_knowledge
    self.voxel = np.load(os.path.join(
        hydra.utils.to_absolute_path(""), f"test_data/{target}/pred_voxel_1.npy"))[:3]
    self.raw_voxel= np.load(os.path.join(
        hydra.utils.to_absolute_path(""), f"test_data/{target}/pred_voxel_1.npy"))[:3]
    
    self.next_atom = {0: [0, 1, 2, 3], 1:[0], 2:[0], 3:[0]}
    self.atom_hands = {0: 4, 1: 3, 2: 4, 3: 4}
    self.center=calc_center_ligand(get_mol(os.path.join(hydra.utils.to_absolute_path(""), f"test_data/{target}/crystal_ligand.mol2")))
    self.cell_size = 0.5
    self.cell_num = 32

    self.voxel = self.standard(self.voxel)
    self.max_cell = np.max(self.voxel.flatten())
    self.current_player = 1
    self.state_voxel = np.zeros((3,32,32,32))
    self.state_index=[]
    self.have_bond = []
    # """
    # self.state_index = [[e[0] for e in np.where(self.voxel==np.max(self.voxel[0]))]]
    # self.have_bond = [0]
    # self.state_voxel[self.state_index[0][0],self.state_index[0][1],self.state_index[0][2],self.state_index[0][3]] = 1
    # """
    self.length = 0

    # self.center = np.array([81.11560283,  5.23239707, 31.73384111])
    # self.valid = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if 0.25*(dx*dx + dy*dy + dz*dz) >= self.min_len*self.min_len and 0.25*(dx*dx + dy*dy + dz*dz) <= self.max_len*self.max_len]
    # self.invalid = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if dx*dx + dy*dy + dz*dz < self.min_len*self.min_len]

    self.bondTypes = [[x for x in BondTypes if t in x.atoms] for t in [0,1,2,3]]
    # self.disable_index = [(self.state_index[0][0]+dx, self.state_index[0][1]+dy, self.state_index[0][2]+dz) for dx, dy, dz in self.invalid]
    self.disable_index = []

    self.invalid2=[[(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if 0.25*(dx*dx + dy*dy + dz*dz) < l*l] for l in MAX_LENGTH]
    # self.invalid = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if dx*dx + dy*dy + dz*dz < self.min_len*self.min_len]
    # self.invalid2 = [(dx, dy, dz) for dx, dy, dz in itertools.product(np.arange(-1*2,2+1,1), repeat=3) if dx*dx + dy*dy + dz*dz < (self.max_len)*(self.max_len)]
    self.connected_atoms = [[]]
    self.qed_list = [0]
    self.qed=-1
    self.vina_score=99
    self.next_actions=[]
    self.next_probs=[]

  def getCurrentPlayer(self):
    return self.current_player

  def getPossibleActions(self):

    if len(self.next_actions) != 0:
      return self.next_actions    

    if len(self.state_index) == 0:  
      possibleActions = []
      next_probs=[]
      self.sumProb=0
      for x in range(16-2,16+2):
          for y in range(16-2,16+2):
            for z in range(16-2,16+2):
              possibleActions.append(InitAtomAdd([x,y,z],0))
              self.sumProb+=self.raw_voxel[0, x, y, z]
              next_probs.append(self.raw_voxel[0, x, y, z])
      self.next_actions=possibleActions
      self.next_probs=next_probs
      return possibleActions    

    possibleActions = []
    self.sumProb=0
    self.next_probs=[]
    next_probs=[]
    for target, state in enumerate(self.state_index):
      t, x, y, z = state
    
      for bondType in self.bondTypes[t]:
          if  bondType.hands + self.have_bond[target] > self.atom_hands[t]-1:
            continue
            
          for dx, dy, dz in bondType.available_voxels:
            new_x = dx + x
            new_y = dy + y
            new_z = dz + z
            new_t = bondType.get(t)
            if (new_x, new_y, new_z) in self.disable_index:
              continue

            connected_atoms = {}
            is_invalid_flag = False
            for index, (atom, bond) in enumerate(zip(self.state_index, self.have_bond)):
              if index == target:
                continue
              t2, x2, y2, z2 = atom
              dist = math.sqrt((new_x-x2)*(new_x-x2)+(new_y-y2)*(new_y-y2)+(new_z-z2)*(new_z-z2))/2
              if dist < MAX_LENGTH[t2] and dist < MAX_LENGTH[new_t]:
                if not new_t in self.next_atom[t2]:
                    is_invalid_flag = True
                    break
              for b2 in self.bondTypes[t2]:
                if b2.get(t2) != new_t:
                  continue
                if b2.isOut(dist):
                  continue
                if b2.isin(dist):
                  connected_atoms[index] = [b2, bond]
                else:
                  is_invalid_flag=True
                  break    
            if len(connected_atoms.keys()) > 1 or is_invalid_flag:
                continue
                
            connected_index = None
            connected_hands = None
            if len(connected_atoms.keys()) == 1:
                connected_index = list(connected_atoms.keys())[0]
                connected_bond, connected_have_atoms = connected_atoms[connected_index]
                connected_hands = connected_bond.hands
                if connected_bond.hands + connected_have_atoms > self.atom_hands[connected_bond.get(t)]-1:
                    continue
                if connected_bond.hands + bondType.hands > self.atom_hands[new_t]-1:
                    continue
            if new_x >= 0 and new_y >= 0 and new_z >= 0 and new_x < 32 and new_y < 32 and new_z < 32:
              if not self.use_knowledge or self.voxel[bondType.get(t), new_x, new_y, new_z] > 0:
                  possibleActions.append(AtomAdd((new_x, new_y, new_z), bondType.get(t), target, bondType.hands, connected_index, connected_hands))
                  self.sumProb+=self.raw_voxel[new_t, new_x, new_y, new_z]
                  next_probs.append(self.raw_voxel[new_t, new_x, new_y, new_z]) 
    self.next_actions=possibleActions
    self.next_probs=next_probs
    return possibleActions
    
  def min_max(self,voxel):
    for i in range(voxel.shape[0]):
        max_num = np.max(voxel[i])
        voxel[i] = voxel[i]/max_num
    return voxel

  def mol_check(self):
        return 0

  def index2point(self):
      points = []
      position = lambda x: x*self.cell_size-0.5*self.cell_size*self.cell_num
      for t, x, y, z in self.state_index:
        points.append([position(x), position(y), position(z), t])
      return points

  def standard(self, voxel, axis=None):
        if axis == None:
          voxel = (voxel-np.mean(voxel.flatten()))/np.std(voxel.flatten())
          return voxel
        for i in range(voxel.shape[0]):
            voxel[i] = (voxel[i] - np.mean(voxel[i].flatten()))/np.std(voxel[i].flatten())
        return voxel
    
  def findUnvisitedChild(self):
    return [c for c in self.children if not c.isVisited]

  def takeAction(self, action):
    if action.__class__ == InitAtomAdd(None, None).__class__ :
      newState = deepcopy(self)
      newState.state_voxel[action.atom_type, action.position[0], action.position[1], action.position[2]] = 1
      newState.state_index.append([action.atom_type, action.position[0], action.position[1], action.position[2]])
      newState.next_actions=[]
      newState.have_bond.append(0)

      for s in newState.state_index:
        t, x, y, z = s
        newState.state_voxel[t, x, y, z] = 1

      return newState

    newState = deepcopy(self)
    newState.state_voxel[action.atom_type, action.position[0], action.position[1], action.position[2]] = 1
    newState.state_index.append([action.atom_type, action.position[0], action.position[1], action.position[2]])
    newBond = action.bond
    disable_index=[]
    if action.connected_index != None:
        newState.have_bond[action.connected_index] = self.have_bond[action.connected_index] + action.connected_bond 
        newBond = newBond + action.connected_bond
        if self.have_bond[action.connected_index] + action.connected_bond  >= self.atom_hands[self.state_index[action.connected_index][0]]-1:
          c_pos = newState.state_index[action.connected_index]
          disable_index.extend([(c_pos[1]+dx, c_pos[2]+dy, c_pos[3]+dz) for dx, dy, dz in self.invalid2[c_pos[0]] if c_pos[1]+dx>=0 and c_pos[2]+dy>=0 and c_pos[3]+dz>=0 and c_pos[1]+dx < 32 and c_pos[2]+dy < 32 and c_pos[3]+dz < 32])
        if action.connected_bond+action.bond>=self.atom_hands[action.atom_type]-1:
            disable_index.extend([(action.position[0]+dx, action.position[1]+dy, action.position[2]+dz) for dx, dy, dz in self.invalid2[action.atom_type] if action.position[0]+dx>=0 and action.position[1]+dy>=0 and action.position[2]+dz>=0 and action.position[0]+dx < 32 and action.position[1]+dy < 32 and action.position[2]+dz < 32])
            
    if action.bond>=self.atom_hands[action.atom_type]-1:
        disable_index.extend([(action.position[0]+dx, action.position[1]+dy, action.position[2]+dz) for dx, dy, dz in self.invalid2[action.atom_type] if action.position[0]+dx>=0 and action.position[1]+dy>=0 and action.position[2]+dz>=0 and action.position[0]+dx < 32 and action.position[1]+dy < 32 and action.position[2]+dz < 32])
            
    # else:
        # disable_index =  [(action.position[0]+dx, action.position[1]+dy, action.position[2]+dz) for dx, dy, dz in self.invalid if action.position[0]+dx>=0 and action.position[1]+dy>=0 and action.position[2]+dz>=0 and action.position[0]+dx < 32 and action.position[1]+dy < 32 and action.position[2]+dz < 32]
    if action.bond+self.have_bond[action.selected_index]>=self.atom_hands[newState.state_index[action.selected_index][0]]-1:
        s_pos = newState.state_index[action.selected_index]
        disable_index.extend([(s_pos[1]+dx, s_pos[2]+dy, s_pos[3]+dz) for dx, dy, dz in self.invalid2[s_pos[0]] if s_pos[1]+dx>=0 and s_pos[2]+dy>=0 and s_pos[3]+dz>=0 and s_pos[1]+dx < 32 and s_pos[2]+dy < 32 and s_pos[3]+dz < 32])

    newState.disable_index.extend(disable_index) 
    newState.disable_index = list(set(newState.disable_index))
    newState.current_player = self.current_player
    newState.have_bond[action.selected_index] = newState.have_bond[action.selected_index] + action.bond
    newState.have_bond.append(newBond)
    newState.next_actions=[]
    # newState.qed_list.append(newState.getQED())

    for s in newState.state_index:
      t, x, y, z = s
      newState.state_voxel[t, x, y, z] = 1
    return newState

  def isTerminal(self):
    # if np.min(self.voxel[self.state_index[-1][0], self.state_index[-1][1], self.state_index[-1][2], self.state_index[-1][3]]) == 0:
    #   qed, sa_score, vina_score = self.get_scores()
    #   with open(f"{self.target}.csv", "a") as file_object:
    #     file_object.write(f"{self.__hash__()}, {qed}, {sa_score}, {vina_score}, {np.sum(self.raw_voxel*self.state_voxel)}, {len(self.state_index)}\n")
    #   return True
    if len(self.state_index)-self.length > 29:
      qed, sa_score, vina_score = self.get_scores()
      with open(f"{self.target}.csv", "a") as file_object:
        file_object.write(f"{self.__hash__()}, {qed}, {sa_score}, {vina_score}, {np.sum(self.raw_voxel*self.state_voxel)}, {len(self.state_index)}\n")
      return True
    # if 0 > self.qed_list[-1]:
    #   with open("sample.csv", "a") as file_object:
    #     file_object.write(f"{self.__hash__()}, {self.qed_list[-1]}, {np.sum(self.voxel*self.state_voxel)}, {len(self.state_index)}\n")
    #   return True
    if len(self.getPossibleActions()) == 0:
      qed, sa_score, vina_score = self.get_scores()
      with open(f"{self.target}.csv", "a") as file_object:
        file_object.write(f"{self.__hash__()}, {qed}, {sa_score}, {vina_score}, {np.sum(self.raw_voxel*self.state_voxel)}, {len(self.state_index)}\n")
      return True
    return False

  def getReward(self):
    # if self.qed == -1:
    #   return 0.5*-self.vina_score
    # if self.vina_score > 200:
    #   return -1 
    if len(self.state_index)>10:
      return -self.vina_score
    else:
      return -99

  def get_scores(self):
    warnings.filterwarnings('ignore')
    points = self.index2point()

    to_xyz_file(origin(points, self.center), f"{self.target}/tmp/test{len(self.state_index)}.xyz")
    obConversion1 = openbabel.OBConversion()
    obConversion2 = openbabel.OBConversion()
    mol = openbabel.OBMol()
    obConversion1.SetInAndOutFormats("xyz", "sdf")
    obConversion2.SetInAndOutFormats("sdf", "pdbqt")
    obConversion1.ReadFile(mol, f"{self.target}/tmp/test{len(self.state_index)}.xyz")
    
    obConversion1.WriteFile(mol, f"{self.target}/tmp/{self.__hash__()}.sdf")
    cmd = pymol.cmd

    cmd.delete("all")
    cmd.load(f"{self.target}/tmp/{self.__hash__()}.sdf")
    cmd.h_add("all")
    cmd.save(f"{self.target}/tmp/{self.__hash__()}.sdf")
    obConversion2.ReadFile(mol, f"{self.target}/tmp/{self.__hash__()}.sdf")
    obConversion2.WriteFile(mol, f"{self.target}/tmp/{self.__hash__()}.pdbqt")
    # subprocess.run(["obabel",f"{self.target}/tmp/test{len(self.state_index)}.xyz","-O",f"{self.target}/tmp/{self.__hash__()}.sdf", "-h"]) 
    # subprocess.run(["obabel",f"{self.target}/tmp/{self.__hash__()}.sdf","-O",f"{self.target}/tmp/{self.__hash__()}.pdbqt", "-h"]) 
    gc.collect()
    try:
      df = PandasTools.LoadSDF(f"{self.target}/tmp/{self.__hash__()}.sdf")
      df["QED"] = df.ROMol.map(QED.qed)
      df['SA_score'] = df.ROMol.map(sascorer.calculateScore)
      if not exists(f"{self.target}/tmp/{self.__hash__()}.pdbqt"):
        vina_score=99
      else:
        vina_score=calc_vina_score(f"{self.target}/tmp/{self.__hash__()}.pdbqt", os.path.join(
            hydra.utils.to_absolute_path(f"test_data/{self.target}/receptor.pdbqt")), self.center)
      
      # print(df["QED"], len(self.state_index))
      self.qed=df["QED"][0]
      self.vina_score=vina_score
      return  df["QED"][0], df["SA_score"][0], vina_score
    except Exception as e:
      # print("error", len(self.state_index))
      print(e)
      self.qed=-1
      self.vina_score=99
      return  -1, 10, 99
    

  def __eq__(self, other):
    return self.__class__ == other.__class__ and self.state_index == other.state_index

  def __hash__(self, state_index=None):
    if state_index == None:
      return hashlib.md5(str(hash((self.__class__, str(self.state_index)))).encode()).hexdigest()
    else:
      return hashlib.md5(str(hash((self.__class__, str(state_index)))).encode()).hexdigest()
    
import hashlib
import subprocess
from utils.preprocess import *
import gc
def origin(atoms, c):
    new_atoms = []
    for a in atoms:
        b = np.array(a[:3] + c).tolist()
        b.append(a[3])
        new_atoms.append(b)
    return new_atoms


@hydra.main(config_name="search_params.yml")
def main(cfg):
    if cfg.target == "":
      sys.exit()
    try:
      os.mkdir(f"{cfg.target}")
      os.mkdir(f"{cfg.target}/trajectory")
      os.mkdir(f"{cfg.target}/tmp")
    except Exception as e:
      print(e)
    state = GridState(cfg.target, cfg.setting.use_knowledge)
    searcher = mcts(iterationLimit=10000, use_knowledge=cfg.setting.use_knowledge)
    pre_reward = -1
    reward = 0
    count = 0
    action = None
    
    center=calc_center_ligand(get_mol(os.path.join(hydra.utils.to_absolute_path(""), f"test_data/{cfg.target}/crystal_ligand.mol2")))
    with open(f"{cfg.target}.csv", "w") as file_object:
      file_object.write("hash, qed, sascore, vina_socre, score, length\n")
    # while pre_reward < reward:
    while count < 1 and len(state.getPossibleActions()) > 0:
        # pre_reward = reward
        if action != None:
            state = state.takeAction(action) 
            state.length = len(state.state_index) 
        to_xyz_file(origin(voxel_to_xyz(state.state_voxel, 0.5, [0.02, 0.02, 0.02, 0.02]), center), f"{cfg.target}/trajectory/test{count}.xyz")
        subprocess.run(["obabel",f"{cfg.target}/trajectory/test{count}.xyz","-O",f"{cfg.target}/trajectory/test{count}.pdb"]) 
        subprocess.run(["rm",f"{cfg.target}/trajectory/test{count}.xyz"]) 
        # print(state.voxel[np.where(state.state_voxel>0)])
        print(state.state_index)
        action = searcher.search(initialState=state)
        #   reward = bestAction["expectedReward"]
        pre_reward = state.getReward()
        print(reward, pre_reward)

        #   action = bestAction["action"]
        count+=1

import signal
import atexit
import time
if __name__ == "__main__":
    main()
  