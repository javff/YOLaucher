--光と闇の竜
--Light and Darkness Dragon (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "This card cannot be Special Summoned. While this card is face-up on the
--field its Attribute is also treated as DARK. When a Spell or Trap Card is activated, or the effect of an Effect Monster is
--activated, that activation is negated and this card loses 500 ATK and DEF. When this card is destroyed and sent to the
--Graveyard, select 1 monster in your Graveyard. Then destroy all cards you control, and Special Summon that monster."
--Diferencias con CardScripts (errata moderna): fuera 'Once per Chain' (SetCountLimit(1,0,EFFECT_COUNT_CODE_CHAIN) y el
--FlagEffect asociado): en 2008 anula TODA activacion de Magia/Trampa/efecto de monstruo, tantas veces en la misma cadena
--como haga falta (y pierde 500 ATK/DEF cada vez). Se mantiene 'If this card has 500 or more ATK and DEF' (sin eso, dos
--LaDD se anularian sin fin) y se excluye su propio efecto (sin el limite por cadena se activaria a si misma).
local s,id=GetID()
function s.initial_effect(c)
	c:AddCannotBeSpecialSummoned()
	--While face-up on the field, this card is also DARK-Attribute
	local e1=Effect.CreateEffect(c)
	e1:SetType(EFFECT_TYPE_SINGLE)
	e1:SetProperty(EFFECT_FLAG_SINGLE_RANGE)
	e1:SetCode(EFFECT_ADD_ATTRIBUTE)
	e1:SetRange(LOCATION_MZONE)
	e1:SetValue(ATTRIBUTE_DARK)
	c:RegisterEffect(e1)
	--Negate the activation of a Spell/Trap Card or monster effect, and if you do, this card loses exactly 500 ATK/DEF
	local e2=Effect.CreateEffect(c)
	e2:SetDescription(aux.Stringid(id,0))
	e2:SetCategory(CATEGORY_NEGATE+CATEGORY_ATKCHANGE+CATEGORY_DEFCHANGE)
	e2:SetType(EFFECT_TYPE_QUICK_F)
	e2:SetProperty(EFFECT_FLAG_DAMAGE_STEP+EFFECT_FLAG_DAMAGE_CAL)
	e2:SetCode(EVENT_CHAINING)
	e2:SetRange(LOCATION_MZONE)
	e2:SetCondition(function(e,tp,eg,ep,ev,re,r,rp) return re:GetHandler()~=e:GetHandler() and (re:IsHasType(EFFECT_TYPE_ACTIVATE) or re:IsMonsterEffect()) end)
	e2:SetTarget(s.negtg)
	e2:SetOperation(s.negop)
	c:RegisterEffect(e2)
	--Destroy all cards you control and Special Summon 1 monster from your GY
	local e3=Effect.CreateEffect(c)
	e3:SetDescription(aux.Stringid(id,1))
	e3:SetCategory(CATEGORY_DESTROY+CATEGORY_SPECIAL_SUMMON)
	e3:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_TRIGGER_F)
	e3:SetProperty(EFFECT_FLAG_CARD_TARGET)
	e3:SetCode(EVENT_TO_GRAVE)
	e3:SetCondition(function(e) return e:GetHandler():IsReason(REASON_DESTROY) end)
	e3:SetTarget(s.sptg)
	e3:SetOperation(s.spop)
	c:RegisterEffect(e3)
	--"Double Snare" validity
	aux.DoubleSnareValidity(c,LOCATION_MZONE)
end
function s.negtg(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return true end
	Duel.SetOperationInfo(0,CATEGORY_NEGATE,eg,1,tp,0)
end
function s.negop(e,tp,eg,ep,ev,re,r,rp)
	local c=e:GetHandler()
	if c:IsFaceup() and c:IsRelateToEffect(e) and c:IsAttackAbove(500) and c:IsDefenseAbove(500)
		and not c:IsStatus(STATUS_BATTLE_DESTROYED) and Duel.GetCurrentChain()==ev+1
		and Duel.NegateActivation(ev) then
		--This card loses exactly 500 ATK/DEF
		local e1=Effect.CreateEffect(c)
		e1:SetType(EFFECT_TYPE_SINGLE)
		e1:SetProperty(EFFECT_FLAG_COPY_INHERIT)
		e1:SetCode(EFFECT_UPDATE_ATTACK)
		e1:SetValue(-500)
		e1:SetReset(RESET_EVENT|RESETS_STANDARD_DISABLE)
		c:RegisterEffect(e1)
		local e2=e1:Clone()
		e2:SetCode(EFFECT_UPDATE_DEFENSE)
		c:RegisterEffect(e2)
	end
end
function s.sptg(e,tp,eg,ep,ev,re,r,rp,chk,chkc)
	if chkc then return chkc:IsLocation(LOCATION_GRAVE) and chkc:IsControler(tp) and chkc:IsCanBeSpecialSummoned(e,0,tp,false,false) end
	if chk==0 then return true end
	Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_SPSUMMON)
	local g=Duel.SelectTarget(tp,Card.IsCanBeSpecialSummoned,tp,LOCATION_GRAVE,0,1,1,nil,e,0,tp,false,false)
	local dg=Duel.GetFieldGroup(tp,LOCATION_ONFIELD,0)
	Duel.SetOperationInfo(0,CATEGORY_DESTROY,dg,#dg,tp,0)
	Duel.SetOperationInfo(0,CATEGORY_SPECIAL_SUMMON,g,1,tp,0)
end
function s.spop(e,tp,eg,ep,ev,re,r,rp)
	local g=Duel.GetFieldGroup(tp,LOCATION_ONFIELD,0)
	if #g>0 then
		Duel.Destroy(g,REASON_EFFECT)
	end
	local tc=Duel.GetFirstTarget()
	if tc and tc:IsRelateToEffect(e) then
		Duel.BreakEffect()
		Duel.SpecialSummon(tc,0,tp,tp,false,false,POS_FACEUP)
	end
end